"""Tunnel-id allocation and destination checks for OpenStack TaaS network forwarding."""

from __future__ import annotations

import ipaddress
import re
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import urlsplit

from crczp.cloud_commons import CrczpException, InvalidTopologyDefinition, Link, NetworkForwarding
from crczp.topology_definition.models import ForwardingDirection

if TYPE_CHECKING:
    from crczp.cloud_commons import TopologyInstance

# tap_mirror tunnel ids are unique per OpenStack project, so each sandbox owns a block of this size.
MAX_TUNNELS_PER_SANDBOX = 1024

_SANDBOX_ID_RE = re.compile(r'-p\d+-s(\d+)$')


class TapMirror(NamedTuple):
    """A tap_mirror of one source port with its allocated tunnel ids."""

    source: Link
    tunnel_id_in: int | None
    tunnel_id_out: int | None


def tunnel_id_base(resource_prefix: str) -> int:
    """
    Return the first tunnel id of the sandbox whose stack name is ``resource_prefix``.

    :raise CrczpException: when the prefix does not end with the sandbox id.
    """
    match = _SANDBOX_ID_RE.search(resource_prefix)
    if not match:
        raise CrczpException(
            f'Cannot allocate network_forwarding tunnel ids: stack name "{resource_prefix}" '
            'does not end with "-p<pool id>-s<sandbox id>".'
        )
    return int(match.group(1)) * MAX_TUNNELS_PER_SANDBOX


def build_tap_mirrors(rule: NetworkForwarding, base: int) -> list[TapMirror]:
    """
    Allocate consecutive tunnel ids from ``base`` to a tap_mirror per source.

    :raise InvalidTopologyDefinition: when the sandbox needs more than its block of ids.
    """
    tap_mirrors = []
    next_id = base
    for source in rule.sources:
        tunnel_id_in = tunnel_id_out = None
        if rule.direction in (ForwardingDirection.IN, ForwardingDirection.BOTH):
            tunnel_id_in = next_id
            next_id += 1
        if rule.direction in (ForwardingDirection.OUT, ForwardingDirection.BOTH):
            tunnel_id_out = next_id
            next_id += 1
        tap_mirrors.append(TapMirror(source, tunnel_id_in, tunnel_id_out))

    needed = next_id - base
    if needed > MAX_TUNNELS_PER_SANDBOX:
        raise InvalidTopologyDefinition(
            f'network_forwarding needs {needed} tunnel ids but only '
            f'{MAX_TUNNELS_PER_SANDBOX} are reserved per sandbox; reduce the number of '
            'mirrored source interfaces.'
        )
    return tap_mirrors


def validate_destination_network(
    network_forwarding: NetworkForwarding | None, topology_instance: TopologyInstance
) -> None:
    """
    Raise when the mirror destination or its network cannot serve only the mirror.

    Every port on that network gets the platform mirror security group, whose only egress
    is UDP to port 67 (DHCP requests), so nothing on it can use the forwarding router for
    anything else. Another host there would silently lose that interface, and the
    destination's address there cannot be probed. For the same reason the destination must be a host
    whose main interface (its first net_mapping) is on another network, as that interface
    carries its sandbox traffic and default route; a router would be cut off.

    :raise InvalidTopologyDefinition: on a destination that is a router or a host whose first
        net_mapping is on the destination network, another host mapped to the network, or a
        monitoring target inside it (TCP/ICMP targets of the destination host, HTTP targets
        by IP).
    """
    if network_forwarding is None:
        return
    destination = network_forwarding.destination
    network = destination.network
    host_names = {host.name for host in topology_instance.get_hosts()}
    if destination.node.name not in host_names:
        raise InvalidTopologyDefinition(
            f'network_forwarding destination "{destination.node.name}" on network '
            f'"{network.name}" must be a host, not a router.'
        )
    main_link = topology_instance.get_node_main_link(destination.node)
    if main_link is None or main_link.network == network:
        raise InvalidTopologyDefinition(
            f'network_forwarding destination "{destination.node.name}": its first net_mapping '
            f'is on the destination network "{network.name}". List its mapping on another network '
            'first: that interface carries its sandbox traffic and default route.'
        )
    for link in topology_instance.get_network_links(network):
        if link.node.name in host_names and link.node.name != destination.node.name:
            raise InvalidTopologyDefinition(
                f'network_forwarding destination network "{network.name}" carries only the '
                f'mirrored traffic for "{destination.node.name}", so no other host can use it, '
                f'but "{link.node.name}" is mapped to it.'
            )

    cidr = ipaddress.ip_network(network.cidr, strict=False)
    for monitored in (
        *topology_instance.get_monitored_hosts_tcp(),
        *topology_instance.get_monitored_hosts_icmp(),
    ):
        if monitored.node != destination.node.name:
            continue
        for target in monitored.targets:
            if target.address and _overlaps(target.address, cidr):
                raise InvalidTopologyDefinition(
                    f'"{destination.node.name}" cannot be monitored at {target.address}: '
                    f'network "{network.name}" carries only its mirrored traffic.'
                )
    http = topology_instance.get_monitored_hosts_http()
    for target in http.targets if http else []:
        if _url_host_in(target.url, cidr):
            raise InvalidTopologyDefinition(
                f'"{target.url}" cannot be monitored: network "{network.name}" carries only '
                'the mirrored traffic of the destination.'
            )


def _overlaps(address: str, network: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    try:
        return ipaddress.ip_network(address, strict=False).overlaps(network)
    except ValueError:
        return False


def _url_host_in(url: str, network: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    try:
        return ipaddress.ip_address(urlsplit(url).hostname or '') in network
    except ValueError:
        return False
