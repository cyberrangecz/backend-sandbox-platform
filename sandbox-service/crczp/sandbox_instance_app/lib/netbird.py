"""NetBird VPN provisioning and teardown for sandbox entrypoints."""

import re
import time
from typing import Any

import structlog
from django.conf import settings

from crczp.sandbox_common_lib.netbird_client import (
    NetbirdApiError,
    NetbirdClient,
    NetbirdConfigError,
    get_netbird_client,
)
from crczp.sandbox_definition_app.lib import definitions
from crczp.sandbox_instance_app.models import Sandbox, SandboxNetbirdAccess, SandboxNetbirdResources

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
) -> None:
    """Populate one role's (or the sandbox's flat) access group and setup key onto ``access``.

    Every entrypoint reachable by ``access``'s role sources its policy and routes
    from this access group; the setup key is what a client resolving to this role
    is issued via the VPN API endpoint.
    """
    access_group_id = client.create_group(access_name)
    access.access_group_id = access_group_id
    access.save()

    access_key_id, access_key_value = client.create_setup_key(
        name=access_name,
        auto_group_ids=[access_group_id],
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

    nameserver_group_id = client.create_nameserver_group(
        name=f'{stack_name}-dns',
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
) -> SandboxNetbirdResources:
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    nbr, _ = SandboxNetbirdResources.objects.get_or_create(
        sandbox=sandbox, entrypoint_host_name=host_name
    )

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
        declared_roles = _get_declared_roles(sandbox)
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

    # Create the sandbox's flat access group + key, unconditionally, exactly as
    # today (role=''), plus one further access group + key per declared role.
    # Every entrypoint's policy and routes source from one or more of these
    # groups, so they must exist before the entrypoint loop. A failure on one
    # role is caught and logged like a per-entrypoint failure: the partial row
    # is left for the end guard (or a later teardown) to clean up, and that
    # role's group is simply absent from what entrypoints can source from.
    access_rows: dict[str, SandboxNetbirdAccess] = {}
    for role in ['', *sorted(declared_roles)]:
        access_name = f'{stack_name}-access' if not role else f'{stack_name}-access-{role}'
        try:
            access, _ = SandboxNetbirdAccess.objects.get_or_create(sandbox=sandbox, role=role)
            # Retained even if provisioning fails partway below, so the end
            # guard/teardown can still find and delete a group already created
            # in Netbird rather than orphan it.
            access_rows[role] = access
            _provision_access(client, access, access_name, key_expiry_seconds)
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

    if any(access.access_group_id for access in access_rows.values()):
        # Provision a DNS nameserver group per successfully provisioned access
        # group (best-effort) before the entrypoint loop. Each rides on its own
        # access group like the policies/routes, so it is torn down by
        # _destroy_access via the persisted group ID; a failure here is
        # contained so the remaining roles and the entrypoints are still
        # provisioned.
        if dns is not None:
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

        for ep in entrypoints:
            host_name = ep.name
            routes = list(ep.routes)
            log = LOG.bind(sandbox_id=sandbox.id, stack_name=stack_name, host=host_name)

            # No accessible_by_roles: every declared role's group (or the flat
            # role='' group, for a role-free pool) — unchanged reachability for
            # an entrypoint that names no team. A declared (possibly empty)
            # list sources from exactly the named roles' groups; a role name
            # with no provisioned row (or an empty list) contributes nothing,
            # so the entrypoint is reachable by no one — fail closed.
            ep_roles = getattr(ep, 'accessible_by_roles', None)
            source_roles = (
                (sorted(declared_roles) if declared_roles else [''])
                if ep_roles is None
                else list(ep_roles)
            )
            access_group_ids = [
                access_rows[role].access_group_id
                for role in source_roles
                if access_rows.get(role) is not None and access_rows[role].access_group_id
            ]

            try:
                nbr = _provision_single_entrypoint(
                    client,
                    sandbox,
                    stack_name,
                    host_name,
                    routes,
                    key_expiry_seconds,
                    access_group_ids,
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
    _teardown_provisioned_resources(client, sandbox, created, list(access_rows.values()))
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
    access_qs = SandboxNetbirdAccess.objects.filter(sandbox=sandbox)
    if not nbr_qs.exists() and not access_qs.exists():
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

    # Tear down every role's access resources last: every entrypoint policy/route
    # that referenced any access group has now been deleted, so none of the
    # groups are in use any longer and all of them can be removed.
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
