"""
Module for topology definition models.
"""

from enum import Enum
from typing import Any, Self

from ruamel.yaml.loader import RoundTripLoader as _RoundTripLoader
from yamlize import Attribute, Dynamic, Map, Object, Sequence, StrList, Typed

from crczp.topology_definition.utils import rename_deprecated_attribute

# ruamel.yaml >=0.19.1 added a max_depth check in Composer.compose_node that
# accesses self.loader.max_depth, but RoundTripLoader (used by yamlize directly)
# does not define this attribute. Patch the class so the depth check is a no-op.
# The assignment deliberately introduces an attribute the class does not declare,
# which is exactly what ty flags, hence the suppression.
if not hasattr(_RoundTripLoader, 'max_depth'):
    _RoundTripLoader.max_depth = None  # ty: ignore[unresolved-attribute]
from crczp.topology_definition.validators import TopologyValidation


class Protocol(Enum):
    """
    Enum for management protocols.
    """

    SSH = 1
    WINRM = 2

    @classmethod
    def create(cls, val: str) -> Self:
        """
        Create Protocol from string.
        """
        try:
            return cls[val.upper()]
        except KeyError as exc:
            raise ValueError(f'Invalid value for Protocol: {val}') from exc


class BaseBox(Object):
    """
    Base box definition.
    """

    image = Attribute(type=str)
    mgmt_user = Attribute(type=str, default='debian')
    # yamlize's Typed is a metaclass whose __new__ builds and returns a separate class,
    # so type.__init__ is never reached; ty still checks the call against its overloads.
    mgmt_protocol = Attribute(
        type=Typed(  # ty: ignore[no-matching-overload]
            Protocol,
            from_yaml=(lambda loader, node, rtd: Protocol.create(loader.construct_object(node))),
            to_yaml=(lambda dumper, data, rtd: dumper.represent_data(data.name)),
        ),
        default=Protocol.SSH,
    )
    # Password for SSH access, for images that cannot receive the injected management key
    # (e.g. appliances without cloud-init). When set, the platform authenticates over SSH
    # with a password instead of the management key.
    mgmt_password = Attribute(type=str, default=None)

    @classmethod
    def from_yaml(cls, loader: Any, node: Any, _rtd: Any = None) -> 'BaseBox':
        """
        Load BaseBox from YAML.
        """
        rename_deprecated_attribute(node.value, 'man_user', 'mgmt_user')
        rename_deprecated_attribute(node.value, 'mng_protocol', 'mgmt_protocol')
        return super().from_yaml(loader, node, _rtd)


class ExtraValues(Map):
    """
    Map for extra values.
    """

    key_type = Typed(str)
    value_type = Dynamic


class Volume(Object):
    """
    Volume definition.
    """

    size = Attribute(type=int, default=None)
    image = Attribute(type=str, default=None)


class VolumeList(Sequence):
    """
    List of volumes.
    """

    item_type = Volume


class Host(Object):
    """
    Host definition.
    """

    name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
    base_box = Attribute(type=BaseBox)
    flavor = Attribute(type=str)
    block_internet = Attribute(type=bool, default=False)
    hidden = Attribute(type=bool, default=False)
    # When False, the host is deployed but excluded from the stage-one networking playbook
    # (for appliances that manage their own networking and reject external reconfiguration).
    managed = Attribute(type=bool, default=True)
    extra = Attribute(type=ExtraValues, default=None)
    volumes = Attribute(
        type=VolumeList, default=None, validator=TopologyValidation.is_volumes_valid
    )

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        name: str,
        base_box: BaseBox,
        flavor: str,
        block_internet: bool,
        hidden: bool,
        volumes: VolumeList | None,
    ) -> None:
        self.name = name
        self.base_box = base_box
        self.flavor = flavor
        self.block_internet = block_internet
        self.hidden = hidden
        self.volumes = volumes


class HostList(Sequence):
    """
    List of hosts.
    """

    item_type = Host


class Router(Object):
    """
    Router definition.
    """

    name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
    base_box = Attribute(type=BaseBox)
    flavor = Attribute(type=str)
    extra = Attribute(type=ExtraValues, default=None)
    hidden = Attribute(type=bool, default=False)

    def __init__(self, name: str, base_box: BaseBox, flavor: str) -> None:
        self.name = name
        self.base_box = base_box
        self.flavor = flavor


class RouterList(Sequence):
    """
    List of routers.
    """

    item_type = Router


class Network(Object):
    """
    Network definition.
    """

    name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
    cidr = Attribute(type=str)
    accessible_by_user = Attribute(type=bool, default=True)
    hidden = Attribute(type=bool, default=False)

    def __init__(self, name: str, cidr: str, accessible_by_user: bool, hidden: bool) -> None:
        self.name = name
        self.cidr = cidr
        self.accessible_by_user = accessible_by_user
        self.hidden = hidden


class WAN(Object):
    """
    WAN definition.
    """

    name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
    cidr = Attribute(type=str)

    def __init__(self, name: str, cidr: str) -> None:
        self.name = name
        self.cidr = cidr


class NetworkList(Sequence):
    """
    List of networks.
    """

    item_type = Network


class NetworkMapping(Object):
    """
    Network mapping definition.
    """

    host = Attribute(type=str)
    network = Attribute(type=str)
    ip = Attribute(type=str)

    def __init__(self, host: str, network: str, ip: str) -> None:
        self.host = host
        self.network = network
        self.ip = ip


class NetworkMappingList(Sequence):
    """
    List of network mappings.
    """

    item_type = NetworkMapping


class RouterMapping(Object):
    """
    Router mapping definition.
    """

    router = Attribute(type=str)
    network = Attribute(type=str)
    ip = Attribute(type=str)

    def __init__(self, router: str, network: str, ip: str) -> None:
        self.router = router
        self.network = network
        self.ip = ip


class RouterMappingList(Sequence):
    """
    List of router mappings.
    """

    item_type = RouterMapping


class Group(Object):
    """
    Group definition.
    """

    name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
    nodes = Attribute(type=StrList, validator=TopologyValidation.validate_group_nodes)

    def __init__(self, name: str) -> None:
        self.name = name
        self.nodes = StrList()

    def add_node(self, node: str) -> None:
        """
        Add node to group.
        """
        self.nodes.append(node)


class GroupList(Sequence):
    """
    List of groups.
    """

    item_type = Group


class TargetTCP(Object):
    """
    TCP monitoring target definition. Exactly one of interface or address must be specified.
    """

    interface = Attribute(type=str, default=None)
    address = Attribute(type=str, default=None)
    port = Attribute(type=int)


class TargetTCPList(Sequence):
    """
    List of TCP targets.
    """

    item_type = TargetTCP


class MonitoringTargetTCP(Object):
    """
    TCP monitoring target node definition.
    """

    node = Attribute(type=str)
    targets = Attribute(type=TargetTCPList, validator=TopologyValidation.validate_targets_tcp)


class MonitoringTargetTCPList(Sequence):
    """
    List of TCP monitoring targets.
    """

    item_type = MonitoringTargetTCP


class TargetICMP(Object):
    """
    ICMP monitoring target definition. Exactly one of interface or address must be specified.
    """

    interface = Attribute(type=str, default=None)
    address = Attribute(type=str, default=None)


class TargetICMPList(Sequence):
    """
    List of ICMP targets.
    """

    item_type = TargetICMP


class MonitoringTargetICMP(Object):
    """
    ICMP monitoring target node definition.
    """

    node = Attribute(type=str)
    targets = Attribute(type=TargetICMPList, validator=TopologyValidation.validate_targets_icmp)


class MonitoringTargetICMPList(Sequence):
    """
    List of ICMP monitoring targets.
    """

    item_type = MonitoringTargetICMP


class TargetHTTP(Object):
    """
    HTTP monitoring target definition.
    """

    url = Attribute(type=str)
    check_string = Attribute(type=str, default=None)


class TargetHTTPList(Sequence):
    """
    List of HTTP targets.
    """

    item_type = TargetHTTP


class MonitoringTargetHTTP(Object):
    """
    HTTP monitoring targets (not bound to a specific node).
    """

    targets = Attribute(type=TargetHTTPList, validator=TopologyValidation.validate_targets_http)


class MonitoringTargets(Object):
    """
    Consolidated monitoring targets definition.
    """

    tcp = Attribute(
        type=MonitoringTargetTCPList,
        validator=TopologyValidation.validate_monitoring_targets_tcp,
        default=None,
    )
    icmp = Attribute(
        type=MonitoringTargetICMPList,
        validator=TopologyValidation.validate_monitoring_targets_icmp,
        default=None,
    )
    http = Attribute(type=MonitoringTargetHTTP, default=None)


class VpnEntrypoint(Object):
    """
    VPN entrypoint definition. Declares a topology host or router that acts as a
    Netbird VPN gateway.
    """

    name = Attribute(type=str, validator=TopologyValidation.validate_vpn_entrypoint_name)
    routes = Attribute(type=StrList, validator=TopologyValidation.validate_vpn_routes)


class VpnEntrypointList(Sequence):
    """
    List of VPN entrypoints.
    """

    item_type = VpnEntrypoint


class VpnDns(Object):
    """
    VPN DNS settings distributed to every client in the sandbox access group.

    ``servers`` is the (non-empty) list of nameserver IPs the clients should use;
    ``search_domains`` is an optional list of DNS search/match domains.
    """

    servers = Attribute(type=StrList, validator=TopologyValidation.validate_vpn_dns_servers)
    search_domains = Attribute(
        type=StrList,
        default=None,
        validator=TopologyValidation.validate_vpn_dns_search_domains,
    )


class Vpn(Object):
    """
    VPN settings for the sandbox (Netbird).

    ``entrypoints`` declares the hosts/routers acting as VPN gateways; ``dns`` is
    an optional DNS configuration applied to the shared access group, i.e. handed
    out to every VPN client of the sandbox.
    """

    entrypoints = Attribute(type=VpnEntrypointList, default=None)
    dns = Attribute(type=VpnDns, default=None)


class ForwardingInterface(Object):
    """
    A single interface in a network-forwarding rule, identified by a host and the
    network it is attached to. Maps 1:1 to a Neutron port / AWS network interface.
    """

    host = Attribute(type=str)
    network = Attribute(type=str)

    def __init__(self, host: str, network: str) -> None:
        self.host = host
        self.network = network


class ForwardingInterfaceList(Sequence):
    """
    List of forwarding interfaces.
    """

    item_type = ForwardingInterface


class NetworkForwardingRule(Object):
    """
    Network traffic forwarding (port mirroring) rule. A copy of the traffic on one
    or more source interfaces is delivered to a single destination interface.

    A topology declares at most one rule, so the resources it renders are named after
    the sandbox prefix alone and need no name of their own.

    ``direction`` selects which traffic is mirrored relative to the source
    (``in``/``out``/``both``).

    A source may be a host or a router. The destination has to be a host and a
    dedicated interface: its host needs at least two interfaces in ``net_mappings``,
    and the first one, which the host routes through, cannot be used. The mirror
    destination is reachable only from the hypervisors, so a host mirroring to its
    default-routing interface would lose the rest of the sandbox.
    """

    sources = Attribute(type=ForwardingInterfaceList)
    destination = Attribute(type=ForwardingInterface)
    direction = Attribute(type=str, default='both')


class TopologyDefinition(Object):  # pylint: disable=too-many-instance-attributes
    """
    Topology definition.
    """

    name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
    hosts = Attribute(type=HostList)
    routers = Attribute(type=RouterList)
    wan = Attribute(type=WAN, default=WAN('wan', '100.100.100.0/24'))
    networks = Attribute(type=NetworkList, validator=TopologyValidation.validate_name_uniqueness)
    # the validation of networks ABOVE is also used to validate the names of the upper four elements
    net_mappings = Attribute(
        type=NetworkMappingList, validator=TopologyValidation.validate_net_mappings
    )
    router_mappings = Attribute(
        type=RouterMappingList, validator=TopologyValidation.validate_router_mappings
    )
    # the validation of router_mappings is also used to validate CIDRs and IP addresses
    groups = Attribute(type=GroupList, validator=TopologyValidation.validate_groups)
    monitoring_targets = Attribute(
        type=MonitoringTargets,
        validator=TopologyValidation.validate_monitoring_targets,
        default=None,
    )
    vpn = Attribute(
        type=Vpn,
        validator=TopologyValidation.validate_vpn,
        default=None,
    )
    network_forwarding = Attribute(
        type=NetworkForwardingRule,
        validator=TopologyValidation.validate_network_forwarding,
        default=None,
    )

    # Class-level defaults so yamlize (which bypasses __init__) finds these attributes
    _indexed: bool = False
    _hosts_index: dict[str, 'Host'] = {}
    _routers_index: dict[str, 'Router'] = {}
    _networks_index: dict[str, 'Network'] = {}

    def __init__(self, name: str, wan: WAN) -> None:
        self.name = name
        self.hosts = HostList()
        self.routers = RouterList()
        self.wan = wan
        self.networks = NetworkList()
        self.net_mappings = NetworkMappingList()
        self.router_mappings = RouterMappingList()
        self.groups = GroupList()
        self.monitoring_targets = None
        self.vpn = None
        self.network_forwarding = None
        self._indexed: bool = False
        self._hosts_index: dict[str, Host] = {}
        self._routers_index: dict[str, Router] = {}
        self._networks_index: dict[str, Network] = {}

    @staticmethod
    def from_file(file: str) -> 'TopologyDefinition':
        """
        Load TopologyDefinition from file.
        """
        with open(file, encoding='utf-8') as f:
            return TopologyDefinition.load(f)

    def index(self) -> None:
        """
        Index hosts, routers and networks.
        """
        self._hosts_index = {h.name: h for h in self.hosts}
        self._routers_index = {r.name: r for r in self.routers}
        self._networks_index = {n.name: n for n in self.networks}
        self._indexed = True

    def find_host_by_name(self, name: str) -> Host | None:
        """
        Find host by name.
        """
        if not getattr(self, '_indexed', False):
            self.index()
        return self._hosts_index.get(name, None)

    def find_router_by_name(self, name: str) -> Router | None:
        """
        Find router by name.
        """
        if not getattr(self, '_indexed', False):
            self.index()
        return self._routers_index.get(name, None)

    def find_network_by_name(self, name: str) -> Network | None:
        """
        Find network by name.
        """
        if not getattr(self, '_indexed', False):
            self.index()
        return self._networks_index.get(name, None)

    def add_host(self, host: Host) -> None:
        """
        Add host to topology.
        """
        self.hosts.append(host)
        self._indexed = False

    def add_router(self, router: Router) -> None:
        """
        Add router to topology.
        """
        self.routers.append(router)
        self._indexed = False

    def add_net_mapping(self, net_mapping: NetworkMapping) -> None:
        """
        Add network mapping to topology.
        """
        self.net_mappings.append(net_mapping)
        self._indexed = False

    def add_router_mappings(self, router_mapping: RouterMapping) -> None:
        """
        Add router mapping to topology.
        """
        self.router_mappings.append(router_mapping)
        self._indexed = False

    def add_group(self, group: Group) -> None:
        """
        Add group to topology.
        """
        self.groups.append(group)
        self._indexed = False


class Container(Object):
    """
    Container definition.
    """

    name = Attribute(type=str)
    image = Attribute(type=str, default='')
    dockerfile = Attribute(type=str, default='')


class ContainerList(Sequence):
    """
    List of containers.
    """

    item_type = Container


class ContainerMapping(Object):
    """
    Container mapping definition.
    """

    container = Attribute(type=str)
    host = Attribute(type=str)
    port = Attribute(type=int)
    hidden = Attribute(type=bool, default=False)


class ContainerMappingList(Sequence):
    """
    List of container mappings.
    """

    item_type = ContainerMapping


class DockerContainers(Object):
    """
    Docker containers definition.
    """

    containers = Attribute(type=ContainerList)
    container_mappings = Attribute(type=ContainerMappingList)
    hide_all = Attribute(type=bool, default=False)

    @staticmethod
    def from_file(file: str) -> 'DockerContainers':
        """
        Load DockerContainers from file.
        """
        with open(file, encoding='utf-8') as f:
            return DockerContainers.load(f)
