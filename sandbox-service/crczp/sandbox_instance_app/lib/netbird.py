"""NetBird VPN provisioning and teardown for sandbox entrypoints."""

import re
import time
from typing import Any

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction

from crczp.cloud_commons import UsersRoles
from crczp.sandbox_common_lib.netbird_client import (
    NetbirdApiError,
    NetbirdClient,
    NetbirdConfigError,
    get_netbird_client,
)
from crczp.sandbox_definition_app.lib import definitions
from crczp.sandbox_instance_app.models import (
    FLAT_ROLE,
    Sandbox,
    SandboxNetbirdAccess,
    SandboxNetbirdEphemeralKey,
    SandboxNetbirdResources,
)

LOG = structlog.get_logger()

# Fallback teardown budget used when the Netbird config does not specify one.
_DEFAULT_TEARDOWN_BUDGET_SECONDS = 60

# Netbird's route `network_id` accepts at most 40 characters.
_NETBIRD_NETWORK_ID_MAX_LEN = 40
# Number of leading characters of the entrypoint host name kept in a network id.
_HOST_NAME_ID_PREFIX_LEN = 8


def _teardown_budget_seconds() -> int:
    """Return the per-sandbox teardown time budget, with a safe fallback.

    Only an unset value falls back to the default; an explicitly configured 0
    (or negative) is honoured so an operator can disable teardown attempts.
    """
    cfg = getattr(settings.CRCZP_CONFIG, 'netbird', None)
    if cfg is None:
        return _DEFAULT_TEARDOWN_BUDGET_SECONDS
    budget = getattr(cfg, 'teardown_budget_seconds', None)
    if budget is None:
        return _DEFAULT_TEARDOWN_BUDGET_SECONDS
    return int(budget)


def _expired(deadline: float | None) -> bool:
    """Whether the teardown time budget has been exhausted (None = no budget)."""
    return deadline is not None and time.monotonic() > deadline


def is_netbird_configured() -> bool:
    """Whether NetBird integration is configured for this deployment.

    This is a cheap, in-memory check (no PAT-file read, no definition fetch) used
    to decide at allocation time whether to enqueue a netbird provisioning job at all.
    """
    return getattr(settings.CRCZP_CONFIG, 'netbird', None) is not None


def _get_vpn_entrypoints(sandbox: Sandbox) -> list[Any]:
    vpn = _get_vpn_settings(sandbox)
    if vpn is None:
        return []
    return getattr(vpn, 'entrypoints', None) or []


def _get_vpn_settings(sandbox: Sandbox) -> Any | None:
    pool = sandbox.allocation_unit.pool
    top_def = definitions.get_definition(pool.definition.url, pool.rev_sha, settings.CRCZP_CONFIG)
    return getattr(top_def, 'vpn', None)


def _get_vpn_dns(sandbox: Sandbox) -> Any | None:
    vpn = _get_vpn_settings(sandbox)
    if vpn is None:
        return None
    return getattr(vpn, 'dns', None)


def _get_declared_roles(sandbox: Sandbox) -> set[str]:
    pool = sandbox.allocation_unit.pool
    top_def = definitions.get_definition(pool.definition.url, pool.rev_sha, settings.CRCZP_CONFIG)
    return top_def.get_declared_roles()


# Matches the zero-padded "-p<pool>-s<sandbox>" suffix that get_stack_name()
# appends (e.g. "-p0000000123-s0000000045"). Anchored to the end so the
# configurable stack-name prefix is never touched.
_STACK_SUFFIX_RE = re.compile(r'-p0*(\d+)-s0*(\d+)$')


def _short_stack_name(stack_name: str) -> str:
    """Strip the zero-padding from the pool/sandbox numbers in a stack name.

    Netbird resource names are derived from the stack name; the full
    zero-padded form (e.g. "...-p0000000123-s0000000045") is too long to be
    fully visible in the Netbird GUI. This collapses it to "...-p123-s45"
    while leaving the prefix intact. A name that doesn't match the expected
    suffix is returned unchanged.
    """
    return _STACK_SUFFIX_RE.sub(r'-p\1-s\2', stack_name)


def _make_network_id(stack_name: str, host_name: str, cidr: str) -> str:
    sanitised = cidr.replace('/', '-').replace('.', '-').replace(':', '-')
    raw = f'{stack_name}-{host_name[:_HOST_NAME_ID_PREFIX_LEN]}-{sanitised}'
    return raw[:_NETBIRD_NETWORK_ID_MAX_LEN]


def _provision_access(
    client: NetbirdClient,
    access: SandboxNetbirdAccess,
    access_name: str,
    key_expiry_seconds: int,
    flat_group_id: str | None = None,
) -> None:
    """Populate one role's (or the sandbox's flat) access group and setup key onto ``access``.

    Every entrypoint reachable by ``access``'s role sources its policy and routes
    from this access group; the setup key is what a client resolving to this role
    is issued via the VPN API endpoint. ``flat_group_id``, when given, is also named
    on the key so its holder stays able to reach entrypoints sourced from the flat
    role alone, homologous to a Network's accessible_by_user: True fallback.
    """
    access_group_id = client.create_group(access_name)
    access.access_group_id = access_group_id
    access.save()

    auto_group_ids = [access_group_id]
    if flat_group_id and flat_group_id != access_group_id:
        auto_group_ids.append(flat_group_id)

    access_key_id, access_key_value = client.create_setup_key(
        name=access_name,
        auto_group_ids=auto_group_ids,
        expires_in_seconds=key_expiry_seconds,
    )
    access.access_setup_key_id = access_key_id
    access.access_setup_key_value = access_key_value
    access.save()


def _provision_dns(
    client: NetbirdClient,
    sandbox: Sandbox,
    stack_name: str,
    access: SandboxNetbirdAccess,
    dns: Any,
) -> None:
    """Create a DNS nameserver group distributed to the sandbox access group.

    ``dns`` carries ``servers`` and optional ``search_domains`` from the topology
    ``vpn.dns`` block. When search domains are given the servers resolve only
    those domains (a non-primary, domain-scoped group with search enabled);
    otherwise they act as the primary resolver for every access-group peer. The
    created group ID is stored on the access row so teardown can remove it.
    """
    access_group_id = access.access_group_id
    servers = [str(s) for s in (getattr(dns, 'servers', None) or [])]
    if not servers or not access_group_id:
        return
    search_domains = [str(d) for d in (getattr(dns, 'search_domains', None) or [])]
    dns_name = f'{stack_name}-dns' if not access.role else f'{stack_name}-dns_{access.role}'

    nameserver_group_id = client.create_nameserver_group(
        name=dns_name,
        servers=servers,
        distribution_group_ids=[access_group_id],
        primary=not search_domains,
        domains=search_domains,
        search_domains_enabled=bool(search_domains),
        description=f'{stack_name} DNS',
    )
    access.dns_nameserver_group_id = nameserver_group_id
    access.save()


def _provision_single_entrypoint(
    client: NetbirdClient,
    sandbox: Sandbox,
    stack_name: str,
    host_name: str,
    routes: list[str],
    key_expiry_seconds: int,
    access_group_ids: list[str],
    source_roles: list[str],
) -> SandboxNetbirdResources:
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    nbr, _ = SandboxNetbirdResources.objects.get_or_create(
        sandbox=sandbox, entrypoint_host_name=host_name
    )
    nbr.set_source_role_list(source_roles)
    nbr.save()

    host_group_id = client.create_group(f'{stack_name}-{host_name}')
    nbr.host_group_id = host_group_id
    nbr.save()

    host_key_id, host_key_value = client.create_setup_key(
        name=f'{stack_name}-{host_name}-host',
        auto_group_ids=[host_group_id],
        expires_in_seconds=key_expiry_seconds,
    )
    nbr.host_setup_key_id = host_key_id
    nbr.host_setup_key_value = host_key_value
    nbr.save()

    if not access_group_ids:
        # No role's access group resolved for this entrypoint (a role named on no
        # provisioned access row, or every named role failing to provision): a
        # route or policy sourced from no group is rejected by Netbird, so none
        # is created, and the entrypoint stays reachable by nobody rather than
        # half-provisioned.
        LOG.warning('netbird_entrypoint_unreachable', sandbox_id=sandbox.id, host=host_name)
        return nbr

    route_ids = []
    for cidr in routes:
        route_id = client.create_route(
            network_id=_make_network_id(stack_name, host_name, cidr),
            cidr=cidr,
            peer_group_ids=[host_group_id],
            client_group_ids=access_group_ids,
            description=f'{stack_name} {host_name} route {cidr}',
        )
        route_ids.append(route_id)
    nbr.set_route_id_list(route_ids)
    nbr.set_route_cidr_list(routes)
    nbr.save()

    policy_id = client.create_policy(
        name=f'{stack_name}-{host_name}-policy',
        source_group_ids=access_group_ids,
        destination_group_ids=[host_group_id],
    )
    nbr.policy_id = policy_id
    nbr.save()

    return nbr


def _provision_role_access(
    client: NetbirdClient,
    sandbox: Sandbox,
    stack_name: str,
    declared_roles: set[str],
    key_expiry_seconds: int,
) -> dict[str, SandboxNetbirdAccess]:
    """Provision the sandbox's flat access group/key (role='') and one per declared role.

    A failure on one role is caught and logged like a per-entrypoint failure: the
    row is kept in the returned mapping (for the end guard or a later teardown to
    find) even without a Netbird group, and that role is simply absent from what
    an entrypoint can source from.

    Every role's key also names the flat access group (provisioned first, role=''),
    so a role holder stays able to reach entrypoints sourced from the flat role alone.
    """
    access_rows: dict[str, SandboxNetbirdAccess] = {}
    flat_group_id: str | None = None
    for role in [FLAT_ROLE, *sorted(declared_roles)]:
        access_name = f'{stack_name}-access' if role == FLAT_ROLE else f'{stack_name}-access_{role}'
        try:
            access, _ = SandboxNetbirdAccess.objects.get_or_create(sandbox=sandbox, role=role)
            access_rows[role] = access
            _provision_access(client, access, access_name, key_expiry_seconds, flat_group_id)
            LOG.info(
                'netbird_access_provisioned',
                sandbox_id=sandbox.id,
                role=role,
                access_group_id=access.access_group_id,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            event = (
                'netbird_provision_failed'
                if isinstance(exc, NetbirdApiError)
                else 'netbird_provision_access_error'
            )
            LOG.warning(event, sandbox_id=sandbox.id, role=role, error=str(exc))
        # Outside the try: unscoped entrypoints source from the flat group whenever
        # it exists, so role keys must name it even if the flat key itself failed.
        if role == FLAT_ROLE and role in access_rows:
            flat_group_id = access_rows[role].access_group_id
    return access_rows


def _provision_role_dns(
    client: NetbirdClient,
    sandbox: Sandbox,
    stack_name: str,
    access_rows: dict[str, SandboxNetbirdAccess],
    dns: Any,
) -> None:
    """Provision a DNS nameserver group for every role with a provisioned access group.

    Best-effort per role: a failure is caught and logged so the remaining roles
    still get DNS distributed.
    """
    for role, access in access_rows.items():
        if not access.access_group_id:
            continue
        try:
            _provision_dns(client, sandbox, stack_name, access, dns)
            LOG.info(
                'netbird_dns_provisioned',
                sandbox_id=sandbox.id,
                role=role,
                nameserver_group_id=access.dns_nameserver_group_id,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            event = (
                'netbird_provision_failed'
                if isinstance(exc, NetbirdApiError)
                else 'netbird_provision_dns_error'
            )
            LOG.warning(event, sandbox_id=sandbox.id, role=role, error=str(exc))


def _source_roles(
    ep_roles: list[str] | None,
    access_rows: dict[str, SandboxNetbirdAccess],
) -> list[str]:
    """Resolve the declared roles whose provisioned access group reaches an entrypoint.

    No declaration (None) names the flat role='', reachable by every player regardless
    of team — homologous to a Network's accessible_by_user: True fallback. A declared
    list names exactly those roles; a role with no provisioned group contributes
    nothing, leaving the entrypoint reachable by no one.
    """
    candidate_roles = [FLAT_ROLE] if ep_roles is None else list(ep_roles)
    return [
        role
        for role in candidate_roles
        if access_rows.get(role) is not None and access_rows[role].access_group_id
    ]


def _source_group_ids(
    source_roles: list[str], access_rows: dict[str, SandboxNetbirdAccess]
) -> list[str]:
    """Resolve the access group ids backing an already-resolved set of source roles."""
    return [access_rows[role].access_group_id for role in source_roles]


def _teardown_provisioned_resources(
    client: NetbirdClient,
    sandbox: Sandbox,
    created: list[SandboxNetbirdResources],
    access_rows: list[SandboxNetbirdAccess],
) -> None:
    """Best-effort teardown of every NetBird object created during provisioning.

    Used by the end guard when the sandbox is deleted mid-provision: every cloud
    object we already created is destroyed so we don't leave orphans behind.
    """
    for nbr in created:
        try:
            _destroy_single_entrypoint(client, nbr)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOG.warning(
                'netbird_provision_abort_destroy_failed',
                sandbox_id=sandbox.id,
                entrypoint_host=nbr.entrypoint_host_name,
                error=str(exc),
            )
    for access in access_rows:
        try:
            _destroy_access(client, access)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOG.warning(
                'netbird_provision_abort_destroy_access_failed',
                sandbox_id=sandbox.id,
                role=access.role,
                error=str(exc),
            )


def provision_netbird_for_sandbox(sandbox: Sandbox) -> None:
    """Best-effort entry point for NetBird provisioning; guaranteed never to raise.

    This is the target of the provisioning RQ job that the user-ansible stage
    depends on. If the job ended FAILED, RQ would defer the user-ansible stage
    forever and wedge the allocation, so every escaping exception is caught here
    and the job is allowed to finish (degraded). The realistic failure modes
    (missing PAT, unreachable NetBird, definition-fetch error) are already
    handled inside the implementation; this outer guard only backstops
    unexpected errors (e.g. a transient DB error in a guard query).
    """
    try:
        _provision_netbird_for_sandbox(sandbox)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOG.warning(
            'netbird_provision_unexpected_error',
            sandbox_id=getattr(sandbox, 'id', None),
            error=str(exc),
        )


def _provision_netbird_for_sandbox(sandbox: Sandbox) -> None:
    """Provision the shared access resources and per-entrypoint NetBird objects.

    Provisioning is best-effort: a misconfigured PAT file or a transient
    definition-fetch error for one sandbox is caught and logged rather than
    propagated (it would otherwise fail the provisioning job and, via the
    user-ansible dependency, wedge the allocation).
    """
    try:
        client = get_netbird_client()
    except NetbirdConfigError as exc:
        LOG.warning(
            'netbird_provision_skipped',
            sandbox_id=sandbox.id,
            reason='config_error',
            error=str(exc),
        )
        return
    if client is None:
        LOG.debug('netbird_provision_skipped', sandbox_id=sandbox.id, reason='no_config')
        return

    try:
        entrypoints = _get_vpn_entrypoints(sandbox)
        dns = _get_vpn_dns(sandbox)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # The topology definition is fetched from git here; any failure must not
        # break allocation, so we skip provisioning rather than crash the worker.
        LOG.warning(
            'netbird_provision_skipped',
            sandbox_id=sandbox.id,
            reason='vpn_settings_lookup_failed',
            error=str(exc),
        )
        return
    if not entrypoints and dns is None:
        LOG.debug('netbird_provision_skipped', sandbox_id=sandbox.id, reason='no_vpn_settings')
        return

    try:
        declared_roles = _get_declared_roles(sandbox)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOG.warning(
            'netbird_provision_skipped',
            sandbox_id=sandbox.id,
            reason='vpn_settings_lookup_failed',
            error=str(exc),
        )
        return

    # Start guard: bail out before making any cloud calls if the sandbox was
    # already deleted (e.g. force-cleanup raced with the provisioning worker).
    if not Sandbox.objects.filter(pk=sandbox.pk).exists():
        LOG.warning('netbird_provision_skipped', sandbox_id=sandbox.id, reason='sandbox_deleted')
        return

    stack_name = _short_stack_name(sandbox.allocation_unit.get_stack_name())
    key_expiry_seconds = settings.CRCZP_CONFIG.netbird.key_expiry_seconds

    LOG.info(
        'netbird_provision_start',
        sandbox_id=sandbox.id,
        stack_name=stack_name,
        entrypoint_count=len(entrypoints),
    )

    created: list[SandboxNetbirdResources] = []

    # Both the flat access group + key (role='') and one further group + key per
    # declared role must exist before the entrypoint loop, which sources each
    # entrypoint's policy and routes from one or more of them.
    access_rows = _provision_role_access(
        client, sandbox, stack_name, declared_roles, key_expiry_seconds
    )

    if any(access.access_group_id for access in access_rows.values()):
        # DNS rides on each access group like the policies/routes, so it is
        # torn down by _destroy_access via the persisted group ID.
        if dns is not None:
            _provision_role_dns(client, sandbox, stack_name, access_rows, dns)

        for ep in entrypoints:
            host_name = ep.name
            routes = list(ep.routes)
            log = LOG.bind(sandbox_id=sandbox.id, stack_name=stack_name, host=host_name)

            ep_roles = getattr(ep, 'accessible_by_roles', None)
            source_roles = _source_roles(ep_roles, access_rows)
            access_group_ids = _source_group_ids(source_roles, access_rows)

            try:
                nbr = _provision_single_entrypoint(
                    client,
                    sandbox,
                    stack_name,
                    host_name,
                    routes,
                    key_expiry_seconds,
                    access_group_ids,
                    source_roles,
                )
                created.append(nbr)
                log.info('netbird_provisioned', policy_id=nbr.policy_id)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # NetbirdApiError is the expected failure; any other exception
                # (e.g. an IntegrityError from a concurrent sandbox.delete() cascade
                # inside _provision_single_entrypoint) is also caught so the loop
                # continues to the end guard instead of crashing the worker.
                event = (
                    'netbird_provision_failed'
                    if isinstance(exc, NetbirdApiError)
                    else 'netbird_provision_entrypoint_error'
                )
                log.warning(event, error=str(exc))
                # Pick up the partially-created row (already persisted before the
                # first API call) so the end guard can tear it down if the sandbox
                # was deleted mid-provision.
                try:
                    partial_nbr = SandboxNetbirdResources.objects.get(
                        sandbox=sandbox, entrypoint_host_name=host_name
                    )
                    created.append(partial_nbr)
                except SandboxNetbirdResources.DoesNotExist:
                    pass

    # End guard: if the sandbox was deleted while we were provisioning, tear down
    # every cloud object we already created so we don't leave orphans behind.
    # The teardown issues slow HTTP calls, so it deliberately runs outside any DB
    # transaction — holding one open across network I/O would pin a connection.
    if Sandbox.objects.filter(pk=sandbox.pk).exists():
        return
    # Same order as destroy_netbird_for_sandbox: role keys name the flat group,
    # so the flat row ('', sorting last) goes after every role row.
    _teardown_provisioned_resources(
        client, sandbox, created, sorted(access_rows.values(), key=lambda a: a.role, reverse=True)
    )
    LOG.warning('netbird_provision_aborted_sandbox_deleted', sandbox_id=sandbox.id)


def _delete_group_peers(
    client: NetbirdClient, group_id: str | None, log: Any, deadline: float | None = None
) -> None:
    """Delete every peer registered into `group_id` (best effort)."""
    if not group_id:
        return
    try:
        peer_ids = client.list_group_peer_ids(group_id)
    except NetbirdApiError as exc:
        log.warning('netbird_list_group_peers_failed', group_id=group_id, error=str(exc))
        return
    for peer_id in peer_ids:
        if _expired(deadline):
            return
        try:
            client.delete_peer(peer_id)
            log.info('netbird_peer_deleted', peer_id=peer_id)
        except NetbirdApiError as exc:
            log.warning('netbird_delete_peer_failed', peer_id=peer_id, error=str(exc))


def _destroy_single_entrypoint(
    client: NetbirdClient, nbr: SandboxNetbirdResources, deadline: float | None = None
) -> None:
    # `deadline` bounds the teardown time (used by destroy_netbird_for_sandbox,
    # which runs in the web-request thread); it is checked before every cloud
    # call so a single entrypoint with many routes/peers cannot overrun. The
    # provision end-guard passes no deadline, so its teardown is never cut short.
    log = LOG.bind(sandbox_id=nbr.sandbox_id, entrypoint_host=nbr.entrypoint_host_name)

    if _expired(deadline):
        return

    if nbr.policy_id:
        try:
            client.delete_policy(nbr.policy_id)
            log.info('netbird_policy_deleted', policy_id=nbr.policy_id)
        except NetbirdApiError as exc:
            log.warning('netbird_delete_policy_failed', error=str(exc))

    for route_id in nbr.get_route_id_list():
        if _expired(deadline):
            return
        try:
            client.delete_route(route_id)
            log.info('netbird_route_deleted', route_id=route_id)
        except NetbirdApiError as exc:
            log.warning('netbird_delete_route_failed', route_id=route_id, error=str(exc))

    if _expired(deadline):
        return

    if nbr.host_setup_key_id:
        try:
            client.delete_setup_key(nbr.host_setup_key_id)
            log.info('netbird_host_key_deleted', key_id=nbr.host_setup_key_id)
        except NetbirdApiError as exc:
            log.warning('netbird_delete_host_key_failed', error=str(exc))

    _delete_group_peers(client, nbr.host_group_id, log, deadline)

    if _expired(deadline):
        return

    if nbr.host_group_id:
        try:
            client.delete_group(nbr.host_group_id)
            log.info('netbird_host_group_deleted', group_id=nbr.host_group_id)
        except NetbirdApiError as exc:
            log.warning('netbird_delete_host_group_failed', error=str(exc))

    nbr.delete()
    log.info('netbird_resources_record_deleted')


def _destroy_access(
    client: NetbirdClient, access: SandboxNetbirdAccess, deadline: float | None = None
) -> None:
    """Tear down one role's (or the sandbox's flat) access key and group.

    Must run only after every entrypoint's policy and routes (which reference
    this access group) have been deleted, otherwise the group delete is rejected
    by Netbird for still being in use. `deadline` bounds the teardown time; the
    provision end-guard passes none, so its teardown is never cut short.
    """
    log = LOG.bind(sandbox_id=access.sandbox_id)

    if _expired(deadline):
        return

    # Delete the DNS nameserver group first: it lists the access group as its
    # distribution group, so the access-group delete below would be rejected
    # while it still exists.
    if access.dns_nameserver_group_id:
        try:
            client.delete_nameserver_group(access.dns_nameserver_group_id)
            log.info(
                'netbird_dns_nameserver_group_deleted',
                nameserver_group_id=access.dns_nameserver_group_id,
            )
        except NetbirdApiError as exc:
            log.warning('netbird_delete_dns_nameserver_group_failed', error=str(exc))

    if _expired(deadline):
        return

    if access.access_setup_key_id:
        try:
            client.delete_setup_key(access.access_setup_key_id)
            log.info('netbird_access_key_deleted', key_id=access.access_setup_key_id)
        except NetbirdApiError as exc:
            log.warning('netbird_delete_access_key_failed', error=str(exc))

    _delete_group_peers(client, access.access_group_id, log, deadline)

    if _expired(deadline):
        return

    if access.access_group_id:
        try:
            client.delete_group(access.access_group_id)
            log.info('netbird_access_group_deleted', group_id=access.access_group_id)
        except NetbirdApiError as exc:
            log.warning('netbird_delete_access_group_failed', error=str(exc))

    access.delete()
    log.info('netbird_access_record_deleted')


def destroy_netbird_for_sandbox(sandbox: Sandbox) -> None:
    """
    Best-effort teardown of all Netbird resources for `sandbox`.

    Runs synchronously in the calling thread (the web-request thread that
    creates the cleanup request), just before the cleanup stages are enqueued.
    Every per-entrypoint teardown is wrapped in its own try/except so a failure
    (or a slow Netbird endpoint that exhausts a single DELETE call's timeout)
    cannot prevent the cleanup stages from being created and enqueued —
    otherwise the user observes "stuck cleanup with three stages in the queue"
    while in reality the stages were never enqueued at all. For the same reason,
    failure to even build the client (e.g. a missing/empty PAT file) is logged
    and swallowed rather than propagated.

    Because teardown is synchronous, the time spent talking to Netbird for one
    sandbox is bounded by ``teardown_budget_seconds``: the deadline is checked
    before every cloud call, so the worst-case overrun is one in-flight call's
    timeout. Once the budget is exceeded the remaining resources are left behind
    (the cloud objects are orphaned, exactly as they already are when Netbird is
    persistently unreachable) so that cleanup is never blocked indefinitely.

    Note: a batched/pool cleanup calls this once per sandbox in a loop, so the
    overall request is bounded by ``teardown_budget_seconds`` times the number
    of sandboxes, not by the per-sandbox budget alone.
    """
    try:
        client = get_netbird_client()
    except NetbirdConfigError as exc:
        LOG.warning(
            'netbird_destroy_skipped', sandbox_id=sandbox.id, reason='config_error', error=str(exc)
        )
        return
    if client is None:
        return

    nbr_qs = SandboxNetbirdResources.objects.filter(sandbox=sandbox)
    # Descending by role: every role-specific key also names the flat group, so
    # the flat row ('', sorting last) must be destroyed after every other row.
    access_qs = SandboxNetbirdAccess.objects.filter(sandbox=sandbox).order_by('-role')
    ephemeral_qs = SandboxNetbirdEphemeralKey.objects.filter(sandbox=sandbox)
    if not nbr_qs.exists() and not access_qs.exists() and not ephemeral_qs.exists():
        return

    deadline = time.monotonic() + _teardown_budget_seconds()

    for nbr in nbr_qs:
        if _expired(deadline):
            LOG.warning(
                'netbird_destroy_time_budget_exceeded', sandbox_id=sandbox.id, stage='entrypoints'
            )
            return
        try:
            _destroy_single_entrypoint(client, nbr, deadline)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOG.warning(
                'netbird_destroy_entrypoint_failed',
                sandbox_id=nbr.sandbox_id,
                entrypoint_host=nbr.entrypoint_host_name,
                error=str(exc),
            )

    # Delete every minted multi-role key before the access groups it references:
    # Netbird rejects deleting a group a setup key still names.
    for ephemeral in ephemeral_qs:
        if _expired(deadline):
            LOG.warning(
                'netbird_destroy_time_budget_exceeded',
                sandbox_id=sandbox.id,
                stage='ephemeral_keys',
            )
            return
        try:
            client.delete_setup_key(ephemeral.key_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOG.warning(
                'netbird_destroy_ephemeral_key_failed',
                sandbox_id=sandbox.id,
                role_set=ephemeral.role_set,
                error=str(exc),
            )
        ephemeral.delete()

    # Tear down every role's access resources last: every entrypoint policy/route
    # and ephemeral key that referenced any access group has now been deleted, so
    # none of the groups are in use any longer and all of them can be removed.
    for access in access_qs:
        if _expired(deadline):
            LOG.warning(
                'netbird_destroy_time_budget_exceeded', sandbox_id=sandbox.id, stage='access'
            )
            return
        try:
            _destroy_access(client, access, deadline)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOG.warning(
                'netbird_destroy_access_failed',
                sandbox_id=sandbox.id,
                role=access.role,
                error=str(exc),
            )


def _flat_access_row(sandbox: Sandbox) -> SandboxNetbirdAccess | None:
    """Return the sandbox's flat (role='') access row, or None if never provisioned."""
    return SandboxNetbirdAccess.objects.filter(sandbox=sandbox, role=FLAT_ROLE).first()


def resolve_vpn_setup_key(sandbox: Sandbox, users_roles: UsersRoles) -> str | None:
    """Resolve the Netbird setup key a requesting user's roles admit for `sandbox`.

    A role-free pool (or a privileged requester when no role is declared) gets
    the sandbox's one shared, stored setup key, unchanged. A requester resolving
    to exactly one role gets that role's own stored key, no new Netbird call. One
    resolving to several roles at once gets a single setup key, minted once and
    then reused, naming every one of those roles' access groups. A requester
    resolving to no declared role gets the flat key when one is provisioned,
    homologous to an unscoped entrypoint's own fallback.
    """
    return resolve_vpn_setup_key_with_reason(sandbox, users_roles)[0]


def resolve_vpn_setup_key_with_reason(
    sandbox: Sandbox, users_roles: UsersRoles
) -> tuple[str | None, str | None]:
    """Resolve `resolve_vpn_setup_key`'s key alongside a reason for a null one.

    The reason is None whenever a key is returned; otherwise one of: 'no_role'
    (the requester holds none of the sandbox's declared roles, and no flat key is
    provisioned either), 'not_provisioned' (the sandbox has no access row at all,
    i.e. no VPN, or the flat row or an admitted role's access group or setup key
    never finished provisioning), or 'error' (minting a multi-role key failed).
    """
    declared_rows = SandboxNetbirdAccess.objects.filter(sandbox=sandbox).exclude(role=FLAT_ROLE)
    if not declared_rows.exists():
        flat_access = _flat_access_row(sandbox)
        if flat_access and flat_access.access_setup_key_value:
            return flat_access.access_setup_key_value, None
        return None, 'not_provisioned'

    if isinstance(users_roles, frozenset):
        resolved_roles = set(users_roles)
    else:
        resolved_roles = set(declared_rows.values_list('role', flat=True))

    if not resolved_roles:
        flat_access = _flat_access_row(sandbox)
        if flat_access and flat_access.access_setup_key_value:
            return flat_access.access_setup_key_value, None
        return None, 'no_role'

    candidate_rows = list(
        SandboxNetbirdAccess.objects.filter(sandbox=sandbox, role__in=resolved_roles)
    )
    if not candidate_rows:
        flat_access = _flat_access_row(sandbox)
        if flat_access and flat_access.access_setup_key_value:
            return flat_access.access_setup_key_value, None
        return None, 'no_role'
    matching_rows = [row for row in candidate_rows if row.access_group_id]
    if not matching_rows:
        return None, 'not_provisioned'
    if len(matching_rows) == 1:
        key = matching_rows[0].access_setup_key_value
        return (key, None) if key else (None, 'not_provisioned')
    role_set = ','.join(sorted(row.role for row in matching_rows))
    key = _mint_ephemeral_vpn_setup_key(sandbox, role_set, matching_rows)
    return (key, None) if key else (None, 'error')


def _mint_ephemeral_vpn_setup_key(
    sandbox: Sandbox, role_set: str, access_rows: list[SandboxNetbirdAccess]
) -> str | None:
    """Resolve the setup key spanning every one of ``access_rows``'s groups.

    Persisted per (sandbox, role_set): a repeat request for the same role
    combination reuses the stored key rather than minting (and eventually
    orphaning) another. Netbird's own ``expires_in_seconds`` bounds its
    lifetime, exactly as every other provisioned key's.
    """
    existing = SandboxNetbirdEphemeralKey.objects.filter(sandbox=sandbox, role_set=role_set).first()
    if existing is not None:
        return existing.key_value

    try:
        client = get_netbird_client()
    except NetbirdConfigError as exc:
        LOG.warning('netbird_ephemeral_key_failed', sandbox_id=sandbox.id, error=str(exc))
        return None
    if client is None:
        LOG.warning('netbird_ephemeral_key_failed', sandbox_id=sandbox.id, error='no_config')
        return None

    stack_name = _short_stack_name(sandbox.allocation_unit.get_stack_name())
    key_expiry_seconds = settings.CRCZP_CONFIG.netbird.key_expiry_seconds
    auto_group_ids = {row.access_group_id for row in access_rows if row.access_group_id}
    flat_access = _flat_access_row(sandbox)
    if flat_access and flat_access.access_group_id:
        auto_group_ids.add(flat_access.access_group_id)
    try:
        key_id, key_value = client.create_setup_key(
            name=f'{stack_name}-access-ephemeral_{role_set.replace(",", "_")}',
            auto_group_ids=list(auto_group_ids),
            expires_in_seconds=key_expiry_seconds,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOG.warning('netbird_ephemeral_key_failed', sandbox_id=sandbox.id, error=str(exc))
        return None
    try:
        with transaction.atomic():
            SandboxNetbirdEphemeralKey.objects.create(
                sandbox=sandbox, role_set=role_set, key_id=key_id, key_value=key_value
            )
    except IntegrityError:
        # A concurrent request for the same role set stored its key first. Hand out
        # that one and drop ours: an unrecorded key would keep Netbird from deleting
        # the groups it names at teardown.
        try:
            client.delete_setup_key(key_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOG.warning(
                'netbird_ephemeral_key_cleanup_failed',
                sandbox_id=sandbox.id,
                key_id=key_id,
                error=str(exc),
            )
        winner = SandboxNetbirdEphemeralKey.objects.filter(
            sandbox=sandbox, role_set=role_set
        ).first()
        return winner.key_value if winner is not None else None
    return key_value


def resolve_vpn_routes(sandbox: Sandbox, users_roles: UsersRoles) -> list[str]:
    """Return the CIDRs of `sandbox`'s entrypoints a requester's roles can reach.

    An entrypoint sourced from the flat role, from no role, or from a row that
    predates this tracking (`None`) is reachable by every requester.
    """
    routes: list[str] = []
    for nbr in sandbox.netbird_resources.all():
        source_roles = nbr.get_source_role_list()
        if (
            source_roles
            and isinstance(users_roles, frozenset)
            and not set(source_roles) & users_roles
        ):
            continue
        routes.extend(nbr.get_route_cidr_list())
    return list(dict.fromkeys(routes))
