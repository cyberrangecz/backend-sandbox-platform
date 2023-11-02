from enum import Enum

from yamlize import Sequence, Object, Attribute, Typed, StrList, Map, Dynamic, IntList

from kypo.topology_definition.validators import TopologyValidation
from kypo.topology_definition.utils import rename_deprecated_attribute


class Protocol(Enum):
    SSH = 1
    WINRM = 2

    @classmethod
    def create(cls, val: str) -> None:
        try:
            return cls[val.upper()]
        except KeyError:
            raise ValueError(f'Invalid value for Protocol: {val}')


class BaseBox(Object):
    image = Attribute(type=str)
    mgmt_user = Attribute(type=str, default='kypo-man')
    mgmt_protocol = Attribute(
        type=Typed(
            Protocol,
            from_yaml=(lambda loader, node, rtd: Protocol.create(loader.construct_object(node))),
            to_yaml=(lambda dumper, data, rtd: dumper.represent_data(data.name))
        ),
        default=Protocol.SSH,
    )

    @classmethod
    def from_yaml(cls, loader, node, _rtd=None):
        rename_deprecated_attribute(node.value, 'man_user', 'mgmt_user')
        rename_deprecated_attribute(node.value, 'mng_protocol', 'mgmt_protocol')
        return super().from_yaml(loader, node, _rtd)


class ExtraValues(Map):
    key_type = Typed(str)
    value_type = Dynamic


class Volume(Object):
    size = Attribute(type=int, default=None)


class VolumeList(Sequence):
    item_type = Volume


class Host(Object):
    name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
    base_box = Attribute(type=BaseBox)
    flavor = Attribute(type=str)
    block_internet = Attribute(type=bool, default=False)
    hidden = Attribute(type=bool, default=False)
    extra = Attribute(type=ExtraValues, default=None)
    volumes = Attribute(type=VolumeList, default=None, validator=TopologyValidation.is_volumes_valid)

    def __init__(self, name, base_box, flavor, block_internet, hidden, volumes):
        self.name = name
        self.base_box = base_box
        self.flavor = flavor
        self.block_internet = block_internet
        self.hidden = hidden
        self.volumes = volumes


class HostList(Sequence):
    item_type = Host


class Router(Object):
    name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
    base_box = Attribute(type=BaseBox)
    flavor = Attribute(type=str)
    extra = Attribute(type=ExtraValues, default=None)

    def __init__(self, name, base_box, flavor):
        self.name = name
        self.base_box = base_box
        self.flavor = flavor


class RouterList(Sequence):
    item_type = Router


class Network(Object):
    name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
    cidr = Attribute(type=str)
    accessible_by_user = Attribute(type=bool, default=True)
    hidden = Attribute(type=bool, default=False)

    def __init__(self, name, cidr, accessible_by_user, hidden):
        self.name = name
        self.cidr = cidr
        self.accessible_by_user = accessible_by_user
        self.hidden = hidden


class WAN(Object):
    name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
    cidr = Attribute(type=str)

    def __init__(self, name, cidr):
        self.name = name
        self.cidr = cidr


class NetworkList(Sequence):
    item_type = Network


class NetworkMapping(Object):
    host = Attribute(type=str)
    network = Attribute(type=str)
    ip = Attribute(type=str)

    def __init__(self, host, network, ip):
        self.host = host
        self.network = network
        self.ip = ip


class NetworkMappingList(Sequence):
    item_type = NetworkMapping


class RouterMapping(Object):
    router = Attribute(type=str)
    network = Attribute(type=str)
    ip = Attribute(type=str)

    def __init__(self, router, network, ip):
        self.router = router
        self.network = network
        self.ip = ip


class RouterMappingList(Sequence):
    item_type = RouterMapping


class Group(Object):
    name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
    nodes = Attribute(type=StrList, validator=TopologyValidation.validate_group_nodes)

    def __init__(self, name):
        self.name = name
        self.nodes = StrList()

    def add_node(self, node):
        self.nodes.append(node)


class GroupList(Sequence):
    item_type = Group


class Target(Object):
    interface = Attribute(type=str)
    port = Attribute(type=int)


class TargetList(Sequence):
    item_type = Target


class MonitoringTarget(Object):
    node = Attribute(type=str)
    targets = Attribute(type=TargetList, validator=TopologyValidation.validate_targets)


class MonitoringTargetList(Sequence):
    item_type = MonitoringTarget


class TopologyDefinition(Object):
    name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
    hosts = Attribute(type=HostList)
    routers = Attribute(type=RouterList)
    wan = Attribute(type=WAN, default=WAN('wan', '100.100.100.0/24'))
    networks = Attribute(type=NetworkList, validator=TopologyValidation.validate_name_uniqueness)
    # the validation of networks ABOVE is also used to validate the names of the upper four elements
    net_mappings = Attribute(type=NetworkMappingList, validator=TopologyValidation.validate_net_mappings)
    router_mappings = Attribute(type=RouterMappingList, validator=TopologyValidation.validate_router_mappings)
    # the validation of router_mappings is also used to validate CIDRs and IP addresses
    groups = Attribute(type=GroupList, validator=TopologyValidation.validate_groups)
    monitoring_targets = Attribute(type=MonitoringTargetList, validator=TopologyValidation.validate_monitoring_targets, default=None)

    _indexed: bool = False
    _hosts_index: dict = {}
    _routers_index: dict = {}
    _networks_index: dict = {}

    def __init__(self, name, wan):
        self.name = name
        self.hosts = HostList()
        self.routers = RouterList()
        self.wan = wan
        self.networks = NetworkList()
        self.net_mappings = NetworkMappingList()
        self.router_mappings = RouterMappingList()
        self.groups = GroupList()
        self.monitoring_targets = MonitoringTargetList()

    @staticmethod
    def from_file(file) -> 'TopologyDefinition':
        return TopologyDefinition.load(open(file, mode='r'))

    def index(self):
        self._hosts_index = {h.name: h for h in self.hosts}
        self._routers_index = {r.name: r for r in self.routers}
        self._networks_index = {n.name: n for n in self.networks}
        _indexed = True

    def find_host_by_name(self, name) -> Host:
        if not self._indexed:
            self.index()
        return self._hosts_index.get(name, None)

    def find_router_by_name(self, name) -> Router:
        if not self._indexed:
            self.index()
        return self._routers_index.get(name, None)

    def find_network_by_name(self, name) -> Network:
        if not self._indexed:
            self.index()
        return self._networks_index.get(name, None)

    def add_host(self, host):
        self.hosts.append(host)

    def add_router(self, router):
        self.routers.append(router)

    def add_net_mapping(self, net_mapping):
        self.net_mappings.append(net_mapping)

    def add_router_mappings(self, router_mapping):
        self.router_mappings.append(router_mapping)

    def add_group(self, group):
        self.groups.append(group)


class Container(Object):
    name = Attribute(type=str)
    image = Attribute(type=str, default="")
    dockerfile = Attribute(type=str, default="")


class ContainerList(Sequence):
    item_type = Container


class ContainerMapping(Object):
    container = Attribute(type=str)
    host = Attribute(type=str)
    port = Attribute(type=int)
    hidden = Attribute(type=bool, default=False)


class ContainerMappingList(Sequence):
    item_type = ContainerMapping


class DockerContainers(Object):
    containers = Attribute(type=ContainerList)
    container_mappings = Attribute(type=ContainerMappingList)
    hide_all = Attribute(type=bool, default=False)

    @staticmethod
    def from_file(file) -> 'DockerContainers':
        return DockerContainers.load(open(file, mode='r'))
