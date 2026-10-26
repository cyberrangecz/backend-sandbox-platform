"""Discovery and deletion of NetBird objects by the short stack name in their names."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from crczp.cloud_commons import StackName, StackNameScheme
from crczp.sandbox_common_lib.netbird_client import NetbirdApiError, NetbirdClient

LOG = structlog.get_logger()

_ROUTE_DESCRIPTION_MARKER = ' route '

_GROUP = 'group'
_SETUP_KEY = 'setup_key'
_ROUTE = 'route'
_POLICY = 'policy'
_NAMESERVER_GROUP = 'nameserver_group'
_PEER = 'peer'


@dataclass(frozen=True)
class NetbirdObject:
    """One NetBird object: its kind, management id and name."""

    kind: str
    object_id: str
    name: str


@dataclass(frozen=True)
class NetbirdDeletionResult:
    """Outcome of deleting one NetBird object; `error` is None on success."""

    kind: str
    object_id: str
    name: str
    error: str | None = None


@dataclass
class NetbirdStackObjects:
    """The NetBird objects belonging to one stack, split by kind."""

    groups: list[NetbirdObject] = field(default_factory=list)
    setup_keys: list[NetbirdObject] = field(default_factory=list)
    routes: list[NetbirdObject] = field(default_factory=list)
    policies: list[NetbirdObject] = field(default_factory=list)
    nameserver_groups: list[NetbirdObject] = field(default_factory=list)


def _stack_of_route(stack_names: StackNameScheme, route: dict[str, Any]) -> StackName | None:
    """Read the stack from a description `{short} {host} route {cidr}`, else from the network id."""
    description = route.get('description') or ''
    stack = stack_names.short_owner_of(description, ' ')
    if stack is not None and _ROUTE_DESCRIPTION_MARKER in description[len(stack.short) + 2 :]:
        return stack
    return stack_names.short_owner_of(route.get('network_id') or '')


def _stack_of_named(stack_names: StackNameScheme, entry: dict[str, Any]) -> StackName | None:
    return stack_names.short_owner_of(entry.get('name') or '')


def _collect(
    stacks: defaultdict[str, NetbirdStackObjects],
    stack_names: StackNameScheme,
    attribute: str,
    kind: str,
    listed: list[dict[str, Any]],
    stack_of: Callable[[dict[str, Any]], StackName | None],
    name_key: str,
) -> None:
    for entry in listed:
        name = str(entry.get(name_key) or '')
        stack = stack_of(entry)
        if stack is None:
            stack_names.report_malformed(name, f'NetBird {kind}s')
        else:
            getattr(stacks[str(stack)], attribute).append(
                NetbirdObject(kind, str(entry['id']), name)
            )


def list_netbird_objects_by_stack(
    client: NetbirdClient, stack_names: StackNameScheme
) -> dict[str, NetbirdStackObjects]:
    """
    Group every listed NetBird object under the stack whose short name opens its name.

    Objects whose name opens with no short stack name of the deployment are left out; one
    that opens with the deployment prefix but names no stack is logged at error level.

    :param client: The NetBird management client
    :param stack_names: The stack names of the deployment
    :return: The objects of each stack, keyed by the full stack name
    """
    stacks: defaultdict[str, NetbirdStackObjects] = defaultdict(NetbirdStackObjects)

    def named(entry: dict[str, Any]) -> StackName | None:
        return _stack_of_named(stack_names, entry)

    def routed(entry: dict[str, Any]) -> StackName | None:
        return _stack_of_route(stack_names, entry)

    _collect(stacks, stack_names, 'groups', _GROUP, client.list_groups(), named, 'name')
    _collect(stacks, stack_names, 'setup_keys', _SETUP_KEY, client.list_setup_keys(), named, 'name')
    _collect(stacks, stack_names, 'routes', _ROUTE, client.list_routes(), routed, 'network_id')
    _collect(stacks, stack_names, 'policies', _POLICY, client.list_policies(), named, 'name')
    _collect(
        stacks,
        stack_names,
        'nameserver_groups',
        _NAMESERVER_GROUP,
        client.list_nameserver_groups(),
        named,
        'name',
    )
    return dict(stacks)


def _delete(
    results: list[NetbirdDeletionResult],
    kind: str,
    object_id: str,
    name: str,
    delete: Callable[[str], None],
) -> None:
    try:
        delete(object_id)
    except NetbirdApiError as exc:
        LOG.warning('netbird_orphan_delete_failed', kind=kind, object_id=object_id, error=str(exc))
        results.append(NetbirdDeletionResult(kind, object_id, name, str(exc)))
    else:
        results.append(NetbirdDeletionResult(kind, object_id, name))


def delete_netbird_stack_objects(
    client: NetbirdClient, objects: NetbirdStackObjects
) -> list[NetbirdDeletionResult]:
    """Delete every object of one stack, one result per object, never raising on API errors.

    The order releases references first: policies and routes, then the nameserver
    groups, setup keys, the peers registered into the stack's groups, and the groups
    last. A 404 counts as deleted.
    """
    results: list[NetbirdDeletionResult] = []
    for policy in objects.policies:
        _delete(results, _POLICY, policy.object_id, policy.name, client.delete_policy)
    for route in objects.routes:
        _delete(results, _ROUTE, route.object_id, route.name, client.delete_route)
    for nameserver in objects.nameserver_groups:
        _delete(
            results,
            _NAMESERVER_GROUP,
            nameserver.object_id,
            nameserver.name,
            client.delete_nameserver_group,
        )
    for setup_key in objects.setup_keys:
        _delete(results, _SETUP_KEY, setup_key.object_id, setup_key.name, client.delete_setup_key)
    emptied_groups: list[NetbirdObject] = []
    for group in objects.groups:
        try:
            peer_ids = client.list_group_peer_ids(group.object_id)
        except NetbirdApiError as exc:
            results.append(NetbirdDeletionResult(_GROUP, group.object_id, group.name, str(exc)))
            continue
        for peer_id in peer_ids:
            _delete(results, _PEER, peer_id, peer_id, client.delete_peer)
        emptied_groups.append(group)
    for group in emptied_groups:
        _delete(results, _GROUP, group.object_id, group.name, client.delete_group)
    return results
