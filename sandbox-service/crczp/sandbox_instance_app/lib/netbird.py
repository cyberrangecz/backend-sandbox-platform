"""NetBird VPN provisioning and teardown for sandbox entrypoints."""

import re
import time
from collections.abc import Callable
from functools import partial
from typing import Any

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction
from rq.job import get_current_job

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
    AllocationRequest,
    Sandbox,
    SandboxAllocationUnit,
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

    Only an unset value falls back to the default; the configuration refuses a budget
    below 1 second.
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

    Every entrypoint reachable by ``access``'s role sources its policy from this
    access group and distributes its routes to it; the setup key is what a client
    resolving to this role is issued via the VPN API endpoint. ``flat_group_id``,
    when given, is also named on the key so its holder stays able to reach
    entrypoints sourced from the flat role alone, homologous to a Network's
    accessible_by_user: True fallback.
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
    nbr: SandboxNetbirdResources,
    stack_name: str,
    routes: list[str],
    key_expiry_seconds: int,
    access_group_ids: list[str],
    source_roles: list[str],
) -> None:
    """Create the entrypoint's host group, host setup key, policy and routes on ``nbr``.

    With no access group to source from, only the host group and key are created.
    The policy comes before the routes because NetBird gives a route only to
    clients that a policy connects to its routing peer: a route without its
    policy would be advertised by the VPN view yet reach no client.
    ``route_cidrs``, which the view advertises, is recorded last.
    """
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    host_name = nbr.entrypoint_host_name
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
        LOG.warning('netbird_entrypoint_unreachable', sandbox_id=nbr.sandbox_id, host=host_name)
        return

    nbr.policy_id = client.create_policy(
        name=f'{stack_name}-{host_name}-policy',
        source_group_ids=access_group_ids,
        destination_group_ids=[host_group_id],
    )
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
        # Saved per route so a failure part-way still records every route
        # created so far for the teardown.
        nbr.set_route_id_list(route_ids)
        nbr.save()
    nbr.set_route_cidr_list(routes)
    nbr.save()


def _discard_failed_entrypoint(
    client: NetbirdClient, nbr: SandboxNetbirdResources, log: Any
) -> bool:
    """Tear down what a failed entrypoint created; return whether its row is kept.

    A failed entrypoint is discarded entirely. The row is kept only when the
    teardown does not finish (e.g. NetBird is down or the PAT was revoked
    mid-run), so the unit teardown can retry with the IDs it holds. Its setup
    key value and route CIDRs are dropped first, so that ansible skips the
    entrypoint VM and the VPN view advertises none of its routes even if the
    teardown raises.
    """
    nbr.host_setup_key_value = None
    nbr.set_route_cidr_list([])
    try:
        if not _destroy_single_entrypoint(client, nbr):
            return False
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log.warning('netbird_provision_cleanup_failed', error=str(exc))
    try:
        nbr.save()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log.warning('netbird_provision_cleanup_failed', error=str(exc))
    return True


def _provision_role_access(
    client: NetbirdClient,
    sandbox: Sandbox,
    stack_name: str,
    declared_roles: set[str],
    key_expiry_seconds: int,
) -> dict[str, SandboxNetbirdAccess]:
    """Provision the sandbox's flat access group/key (role='') and one per declared role.

    A failure on one role is caught and logged: the row is kept in the returned
    mapping (for the end guard or a later teardown to find) even without a Netbird
    group, and that role is simply absent from what an entrypoint can source from.

    Every role's key also names the flat access group (provisioned first, role=''),
    so a role holder stays able to reach entrypoints sourced from the flat role alone.
    """
    access_rows: dict[str, SandboxNetbirdAccess] = {}
    flat_group_id: str | None = None
    for role in [FLAT_ROLE, *sorted(declared_roles)]:
        access_name = f'{stack_name}-access' if role == FLAT_ROLE else f'{stack_name}-access_{role}'
        try:
            access, _ = SandboxNetbirdAccess.objects.get_or_create(
                sandbox=sandbox,
                role=role,
                defaults={'allocation_unit_id': sandbox.allocation_unit_id},
            )
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

    Run as an RQ job, it does nothing once its allocation request no longer tracks the
    job, and clears the tracked job id when it ends, whatever the outcome.
    """
    job = get_current_job()
    try:
        if job is None or _is_tracked_by_allocation_request(sandbox, job.id):
            _provision_netbird_for_sandbox(sandbox)
        else:
            LOG.info(
                'netbird_provision_skipped', sandbox_id=sandbox.id, reason='job_no_longer_tracked'
            )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOG.warning(
            'netbird_provision_unexpected_error',
            sandbox_id=getattr(sandbox, 'id', None),
            error=str(exc),
        )
    if job is not None:
        _forget_job(sandbox, job.id)


def _is_tracked_by_allocation_request(sandbox: Sandbox, job_id: str) -> bool:
    """Whether the allocation request of the sandbox's unit tracks the given NetBird job."""
    return AllocationRequest.objects.filter(
        allocation_unit_id=sandbox.allocation_unit_id, netbird_job_id=job_id
    ).exists()


def _forget_job(sandbox: Sandbox, job_id: str) -> None:
    """Clear the NetBird job id of the sandbox's allocation request when it is the given job."""
    try:
        AllocationRequest.objects.filter(
            allocation_unit_id=sandbox.allocation_unit_id, netbird_job_id=job_id
        ).update(netbird_job_id=None)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOG.warning(
            'netbird_job_forget_failed',
            sandbox_id=getattr(sandbox, 'id', None),
            job_id=job_id,
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
    # already deleted (e.g. a cleanup raced with the provisioning worker).
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
    # entrypoint's policy from one or more of them and distributes its routes to them.
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

            nbr: SandboxNetbirdResources | None = None
            try:
                nbr, _ = SandboxNetbirdResources.objects.get_or_create(
                    sandbox=sandbox,
                    entrypoint_host_name=host_name,
                    defaults={'allocation_unit_id': sandbox.allocation_unit_id},
                )
                _provision_single_entrypoint(
                    client,
                    nbr,
                    stack_name,
                    routes,
                    key_expiry_seconds,
                    access_group_ids,
                    source_roles,
                )
                created.append(nbr)
                log.info('netbird_provisioned', policy_id=nbr.policy_id)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # NetbirdApiError is the expected failure; any other exception
                # (e.g. an IntegrityError from a concurrent sandbox.delete() cascade)
                # is also caught so the loop continues to the end guard instead of
                # crashing the worker.
                event = (
                    'netbird_provision_failed'
                    if isinstance(exc, NetbirdApiError)
                    else 'netbird_provision_entrypoint_error'
                )
                log.warning(event, error=str(exc))
                # The in-memory handle still holds every ID created so far, even
                # if a cascade has already deleted the row.
                if nbr is not None and _discard_failed_entrypoint(client, nbr, log):
                    created.append(nbr)

    # End guard: if the sandbox was deleted while we were provisioning, tear down
    # every cloud object we already created so we don't leave orphans behind.
    # The teardown issues slow HTTP calls, so it deliberately runs outside any DB
    # transaction — holding one open across network I/O would pin a connection.
    if Sandbox.objects.filter(pk=sandbox.pk).exists():
        return
    # Same order as destroy_netbird_for_unit: role keys name the flat group,
    # so the flat row ('', sorting last) goes after every role row.
    _teardown_provisioned_resources(
        client, sandbox, created, sorted(access_rows.values(), key=lambda a: a.role, reverse=True)
    )
    LOG.warning('netbird_provision_aborted_sandbox_deleted', sandbox_id=sandbox.id)


BUDGET_EXCEEDED_FAILURE = 'teardown time budget exceeded'


def _delete_object(
    failures: list[str], log: Any, kind: str, object_id: str, delete: Callable[[str], None]
) -> None:
    """Delete one NetBird object, appending why a failed deletion failed to ``failures``."""
    try:
        delete(object_id)
    except NetbirdApiError as exc:
        log.warning('netbird_delete_failed', kind=kind, object_id=object_id, error=str(exc))
        failures.append(f'{kind} {object_id}: {exc}')
    else:
        log.info('netbird_object_deleted', kind=kind, object_id=object_id)


def _delete_group_peers(
    client: NetbirdClient,
    group_id: str | None,
    log: Any,
    failures: list[str],
    deadline: float | None = None,
) -> None:
    """Delete every peer registered into `group_id`, appending each failure to ``failures``."""
    if not group_id:
        return
    try:
        peer_ids = client.list_group_peer_ids(group_id)
    except NetbirdApiError as exc:
        log.warning('netbird_list_group_peers_failed', group_id=group_id, error=str(exc))
        failures.append(f'peers of group {group_id}: {exc}')
        return
    for peer_id in peer_ids:
        if _expired(deadline):
            failures.append(BUDGET_EXCEEDED_FAILURE)
            return
        _delete_object(failures, log, 'peer', peer_id, client.delete_peer)


def _destroy_single_entrypoint(
    client: NetbirdClient, nbr: SandboxNetbirdResources, deadline: float | None = None
) -> list[str]:
    """
    Tear down the NetBird objects of one entrypoint and delete its record once none failed.

    `deadline` bounds the teardown time; it is checked before every cloud call so a single
    entrypoint with many routes/peers cannot overrun. Without a deadline the teardown is
    never cut short.

    :returns: Why each deletion that failed or was cut short failed; empty once the
        entrypoint is gone. The record stays while any remain, for a later retry.
    """
    log = LOG.bind(sandbox_id=nbr.sandbox_id, entrypoint_host=nbr.entrypoint_host_name)
    failures: list[str] = []

    if _expired(deadline):
        return [BUDGET_EXCEEDED_FAILURE]

    if nbr.policy_id:
        _delete_object(failures, log, 'policy', nbr.policy_id, client.delete_policy)

    for route_id in nbr.get_route_id_list():
        if _expired(deadline):
            return [*failures, BUDGET_EXCEEDED_FAILURE]
        _delete_object(failures, log, 'route', route_id, client.delete_route)

    if _expired(deadline):
        return [*failures, BUDGET_EXCEEDED_FAILURE]

    if nbr.host_setup_key_id:
        _delete_object(failures, log, 'setup key', nbr.host_setup_key_id, client.delete_setup_key)

    _delete_group_peers(client, nbr.host_group_id, log, failures, deadline)

    if _expired(deadline):
        return [*failures, BUDGET_EXCEEDED_FAILURE]

    if nbr.host_group_id:
        _delete_object(failures, log, 'group', nbr.host_group_id, client.delete_group)

    if not failures:
        nbr.delete()
        log.info('netbird_resources_record_deleted')
    return failures


def _destroy_access(
    client: NetbirdClient, access: SandboxNetbirdAccess, deadline: float | None = None
) -> list[str]:
    """Tear down one role's (or the sandbox's flat) access key and group, then its record.

    Must run only after every entrypoint's policy and routes (which reference
    this access group) have been deleted, otherwise the group delete is rejected
    by Netbird for still being in use. `deadline` bounds the teardown time; the
    provision end-guard passes none, so its teardown is never cut short.

    :returns: Why each deletion that failed or was cut short failed; empty once the access
        is gone. The record stays while any remain, for a later retry.
    """
    log = LOG.bind(sandbox_id=access.sandbox_id)
    failures: list[str] = []

    if _expired(deadline):
        return [BUDGET_EXCEEDED_FAILURE]

    # Delete the DNS nameserver group first: it lists the access group as its
    # distribution group, so the access-group delete below would be rejected
    # while it still exists.
    if access.dns_nameserver_group_id:
        _delete_object(
            failures,
            log,
            'nameserver group',
            access.dns_nameserver_group_id,
            client.delete_nameserver_group,
        )

    if _expired(deadline):
        return [*failures, BUDGET_EXCEEDED_FAILURE]

    if access.access_setup_key_id:
        _delete_object(
            failures, log, 'setup key', access.access_setup_key_id, client.delete_setup_key
        )

    _delete_group_peers(client, access.access_group_id, log, failures, deadline)

    if _expired(deadline):
        return [*failures, BUDGET_EXCEEDED_FAILURE]

    if access.access_group_id:
        _delete_object(failures, log, 'group', access.access_group_id, client.delete_group)

    if not failures:
        access.delete()
        log.info('netbird_access_record_deleted')
    return failures


def _destroy_ephemeral_key(
    client: NetbirdClient, ephemeral_key: SandboxNetbirdEphemeralKey
) -> list[str]:
    """Delete one minted multi-role setup key, then its record.

    :returns: Why the deletion failed; empty once the key is gone. The record stays on a
        failure, for a later retry.
    """
    log = LOG.bind(sandbox_id=ephemeral_key.sandbox_id, role_set=ephemeral_key.role_set)
    failures: list[str] = []
    _delete_object(failures, log, 'setup key', ephemeral_key.key_id, client.delete_setup_key)
    if not failures:
        ephemeral_key.delete()
        log.info('netbird_ephemeral_key_record_deleted')
    return failures


def destroy_netbird_for_unit(unit: SandboxAllocationUnit) -> list[str]:
    """
    Tear down the Netbird resources recorded for the allocation unit,
    those of every sandbox it held included.

    A unit with no recorded resources, and a deployment without NetBird, are left alone
    without building a client. The record of an entrypoint or access is deleted only once
    every object it names is gone, so a later teardown of the unit retries what is left.

    The time spent talking to Netbird for one unit is bounded by
    ``teardown_budget_seconds``: the deadline is checked before every cloud call,
    so the worst-case overrun is one in-flight call's timeout. Once the budget is
    exceeded the remaining resources and their records are left behind.

    :returns: Why each teardown that failed or was cut short failed; empty once every
        recorded object is gone. A client that cannot be built counts as a failure.
    """
    resources = list(SandboxNetbirdResources.objects.filter(allocation_unit=unit))
    ephemeral_keys = list(SandboxNetbirdEphemeralKey.objects.filter(allocation_unit=unit))
    # Descending by role: every role-specific key also names the flat group, so
    # the flat row ('', sorting last) must be destroyed after every other row.
    accesses = list(SandboxNetbirdAccess.objects.filter(allocation_unit=unit).order_by('-role'))
    if not resources and not ephemeral_keys and not accesses:
        return []

    try:
        client = get_netbird_client()
    except NetbirdConfigError as exc:
        LOG.warning(
            'netbird_destroy_skipped', unit_id=unit.id, reason='config_error', error=str(exc)
        )
        return [f'NetBird client unavailable: {exc}']
    if client is None:
        return []

    deadline = time.monotonic() + _teardown_budget_seconds()
    failures: list[str] = []

    for nbr in resources:
        if _expired(deadline):
            LOG.warning(
                'netbird_destroy_time_budget_exceeded', unit_id=unit.id, stage='entrypoints'
            )
            return [*failures, BUDGET_EXCEEDED_FAILURE]
        failures += _guarded(
            partial(_destroy_single_entrypoint, client, nbr, deadline),
            LOG.bind(unit_id=unit.id, entrypoint_host=nbr.entrypoint_host_name),
        )

    # Delete every minted multi-role key before the access groups it references:
    # Netbird rejects deleting a group a setup key still names.
    for ephemeral_key in ephemeral_keys:
        if _expired(deadline):
            LOG.warning(
                'netbird_destroy_time_budget_exceeded', unit_id=unit.id, stage='ephemeral_keys'
            )
            return [*failures, BUDGET_EXCEEDED_FAILURE]
        failures += _guarded(
            partial(_destroy_ephemeral_key, client, ephemeral_key),
            LOG.bind(unit_id=unit.id, role_set=ephemeral_key.role_set),
        )

    # Tear down every role's access resources last: every entrypoint policy/route
    # and ephemeral key that referenced any access group has now been deleted, so
    # none of the groups are in use any longer and all of them can be removed.
    for access in accesses:
        if _expired(deadline):
            LOG.warning('netbird_destroy_time_budget_exceeded', unit_id=unit.id, stage='access')
            return [*failures, BUDGET_EXCEEDED_FAILURE]
        failures += _guarded(
            partial(_destroy_access, client, access, deadline),
            LOG.bind(unit_id=unit.id, role=access.role),
        )
    return failures


def _guarded(teardown: Callable[[], list[str]], log: Any) -> list[str]:
    """Run one teardown, turning an unexpected exception into a failure."""
    try:
        return teardown()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log.warning('netbird_destroy_failed', error=str(exc))
        return [str(exc) or type(exc).__name__]


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
                sandbox=sandbox,
                allocation_unit_id=sandbox.allocation_unit_id,
                role_set=role_set,
                key_id=key_id,
                key_value=key_value,
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
