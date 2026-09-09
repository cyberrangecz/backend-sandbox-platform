"""Build OpenStack TaaS ``tap_mirror`` render structures from the resolved forwarding rule.

The Terraform template renders network forwarding (port mirroring) purely from the
structures produced here, so all name derivation and tunnel-id allocation lives in
Python where it is unit-testable.

Every resource named here is ``{resource_prefix}-tapm-*``, after the TaaS ``tap_mirror``
object, which keeps the resources this feature adds to a sandbox distinguishable from the
topology-derived ones; the AWS driver does the same with its ``tm*`` family.
"""

from __future__ import annotations

import ipaddress
import re
from typing import TYPE_CHECKING, NamedTuple

from crczp.cloud_commons import CrczpException, NetworkForwarding

if TYPE_CHECKING:
    from crczp.cloud_commons import TopologyInstance

# Number of tunnel-id slots reserved per sandbox. A tap_mirror ``directions.in``/``out``
# id must be unique within the whole OpenStack project, so it is derived from the
# (project-unique) sandbox id: ``sandbox_id * MAX_TUNNELS_PER_SANDBOX + slot``. GRE keys
# are 32-bit, leaving ample room; ERSPAN v1 has a much smaller id space and is only safe
# for low sandbox ids.
MAX_TUNNELS_PER_SANDBOX = 1024

# Offset in a mirror-destination subnet the sandbox router interface is pinned to. Offsets
# 0-2 belong to the platform: 0 is the network address; 1 is the topology router VM, which
# the template renders as the subnet's gateway_ip and Neutron therefore keeps out of the
# allocation pool; and Neutron's own metadata/DHCP port (device_owner "network:distributed"
# on ML2/OVN), created together with the subnet and so before any Terraform port, takes the
# lowest free pool address. Offset 3 is the first address neither can claim, and the one
# IPAM already hands this port, so pinning it only makes today's outcome deterministic.
# Left unpinned the port races the topology ports on the same subnet -- nothing orders them
# -- and can take an address a host explicitly asks for.
ROUTER_INTERFACE_OFFSET = 3

# Router VM, metadata/DHCP address, one destination host and this interface: a /29 at least.
# Counted in addresses rather than prefix bits to stay correct for IPv6, and it is what makes
# indexing by the offset safe -- a /30's offset 3 is the broadcast address and a /31 has no
# such address at all.
_MIN_DESTINATION_ADDRESSES = 8

# Matches the zero-padded "-p<pool>-s<sandbox>" suffix that get_stack_name() appends.
_STACK_SUFFIX_RE = re.compile(r'-p0*(\d+)-s0*(\d+)$')

_NAME_SEP = '-'


class RouterInterface(NamedTuple):
    """A router interface attaching a mirror-destination network to the sandbox router."""

    iface_name: str
    port_name: str
    network_name: str
    subnet_name: str
    fixed_ip: str


class FloatingIp(NamedTuple):
    """A floating IP allocated for and associated with a mirror-destination port."""

    fip_name: str
    assoc_name: str
    dest_port_name: str
    router_iface_name: str


class TapMirror(NamedTuple):
    """A tap_mirror mirroring one source port into a destination's floating IP."""

    name: str
    source_port_name: str
    fip_name: str
    mirror_type: str
    tunnel_id_in: int | None
    tunnel_id_out: int | None


class TapMirrorPlan(NamedTuple):
    """Everything the Terraform template needs to render network forwarding."""

    router_name: str
    secgroup_name: str
    router_interface: RouterInterface | None
    floating_ip: FloatingIp | None
    tap_mirrors: list[TapMirror]

    @property
    def destination_port_names(self) -> frozenset[str]:
        """
        Return the ports that receive mirrored traffic.

        The template renders these with the mirror-destination security group instead of the
        topology one, which is what keeps their floating IPs reachable from the hypervisors
        only.
        """
        if self.floating_ip is None:
            return frozenset()
        return frozenset({self.floating_ip.dest_port_name})


def router_interface_ip(network_name: str, cidr: str) -> str:
    """
    Return the address the mirror-destination router-interface port is pinned to.

    :raise CrczpException: when the cidr cannot be parsed, or the subnet is too small to
        spare an address for the router interface.
    """
    try:
        subnet = ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        raise CrczpException(
            f'network_forwarding destination network "{network_name}" has an unparseable '
            f'cidr "{cidr}": {exc}'
        ) from exc

    if subnet.num_addresses < _MIN_DESTINATION_ADDRESSES:
        raise CrczpException(
            f'network_forwarding destination network "{network_name}" ({cidr}) is too small. '
            'Mirroring attaches a router interface to the destination subnet, which needs an '
            'address besides the gateway, the DHCP/metadata address and the destination host. '
            'Use a prefix of /29 or larger.'
        )
    return str(subnet[ROUTER_INTERFACE_OFFSET])


def validate_router_interface_addresses(
    network_forwarding: NetworkForwarding | None, topology_instance: TopologyInstance
) -> None:
    """
    Raise when a mirror-destination network assigns the address reserved for its router interface.

    Nothing in the topology definition schema reserves that address, so without this check a
    clash surfaces only as a Neutron IpAddressAlreadyAllocated mid-apply.

    :raise CrczpException: on a clash, or on a destination network the offset does not fit.
    """
    if network_forwarding is None:
        return
    network = network_forwarding.destination.network
    name = network.name
    reserved = ipaddress.ip_address(router_interface_ip(name, network.cidr))
    for link in topology_instance.get_network_links(network):
        # Compared as addresses, not strings, so an equivalent-but-differently-spelled
        # mapping (IPv6 shorthand against the expanded form) cannot slip past.
        if link.ip and ipaddress.ip_address(link.ip) == reserved:
            raise CrczpException(
                f'network_forwarding reserves {reserved} on network "{name}" '
                f'({network.cidr}) for the router interface that exposes the mirror '
                f'destination, but "{link.node.name}" is mapped to it. Offsets 0-'
                f'{ROUTER_INTERFACE_OFFSET - 1} are the network address, the router VM '
                '(the subnet gateway) and the OpenStack DHCP/metadata address, so assign '
                f'"{link.node.name}" a higher address on "{name}".'
            )


def tunnel_id_base(resource_prefix: str) -> int:
    """Return the first tunnel id reserved for this sandbox (0 when the prefix has no suffix)."""
    match = _STACK_SUFFIX_RE.search(resource_prefix)
    if not match:
        return 0
    sandbox_id = int(match.group(2))
    return sandbox_id * MAX_TUNNELS_PER_SANDBOX


def build_tap_mirror_plan(
    network_forwarding: NetworkForwarding | None, resource_prefix: str, mirror_type: str
) -> TapMirrorPlan:
    """
    Build the render-ready tap_mirror plan from the resolved forwarding rule.

    A topology declares at most one rule, so the plan holds one router interface (on the
    destination network) and one floating IP (on the destination port). Tunnel ids are
    allocated project-uniquely from the sandbox id.

    ``mirror_type`` is the deployment-wide TaaS tunnel encapsulation (``gre``/``erspanv1``),
    supplied by the sandbox-service config rather than the topology.

    Every sandbox gets its own router, so mirror-destination subnets of different sandboxes
    never collide even though a pool renders the same CIDRs for all of them.

    The router interface is pinned to ROUTER_INTERFACE_OFFSET of the destination subnet; see
    validate_router_interface_addresses for the clash check against the topology's own addresses.

    :raise CrczpException: when a sandbox needs more tunnel ids than the reserved slot count,
        or the destination network is too small to spare an address for its router interface.
    """

    def prefixed(name: str) -> str:
        return f'{resource_prefix}{_NAME_SEP}{name}'

    empty_plan = TapMirrorPlan(
        router_name=prefixed('tapm-rtr'),
        secgroup_name=prefixed('tapm-sg'),
        router_interface=None,
        floating_ip=None,
        tap_mirrors=[],
    )
    if network_forwarding is None:
        return empty_plan

    rule = network_forwarding
    dest_network = rule.destination.network.name
    router_interface = RouterInterface(
        iface_name=prefixed(f'tapm-ri-{dest_network}'),
        port_name=prefixed(f'tapm-rp-{dest_network}'),
        network_name=prefixed(dest_network),
        subnet_name=prefixed(f'{dest_network}-subnet'),
        fixed_ip=router_interface_ip(dest_network, rule.destination.network.cidr),
    )
    floating_ip = FloatingIp(
        fip_name=prefixed(f'tapm-fip-{rule.destination.name}'),
        assoc_name=prefixed(f'tapm-fipa-{rule.destination.name}'),
        dest_port_name=prefixed(rule.destination.name),
        router_iface_name=router_interface.iface_name,
    )

    base = tunnel_id_base(resource_prefix)
    tap_mirrors: list[TapMirror] = []
    slot = 0
    for src_index, source in enumerate(rule.sources):
        tunnel_id_in = tunnel_id_out = None
        if rule.direction in ('in', 'both'):
            tunnel_id_in = base + slot
            slot += 1
        if rule.direction in ('out', 'both'):
            tunnel_id_out = base + slot
            slot += 1
        tap_mirrors.append(
            TapMirror(
                name=prefixed(f'tapm-{src_index}'),
                source_port_name=prefixed(source.name),
                fip_name=floating_ip.fip_name,
                mirror_type=mirror_type,
                tunnel_id_in=tunnel_id_in,
                tunnel_id_out=tunnel_id_out,
            )
        )

    if slot > MAX_TUNNELS_PER_SANDBOX:
        raise CrczpException(
            f'network_forwarding needs {slot} tunnel ids but only {MAX_TUNNELS_PER_SANDBOX} '
            'are reserved per sandbox; reduce the number of mirrored source interfaces.'
        )

    return empty_plan._replace(
        router_interface=router_interface,
        floating_ip=floating_ip,
        tap_mirrors=tap_mirrors,
    )
