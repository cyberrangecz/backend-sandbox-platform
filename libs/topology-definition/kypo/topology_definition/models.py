from enum import Enum

from yamlize import Sequence, Object, Attribute, Typed, StrList


class Provider(Enum):
    OpenStack = 1
    Vagrant = 2


class BaseBox(Object):
    image = Attribute(type=str)
    man_user = Attribute(type=str, default='kypo-man')


class Host(Object):
    name = Attribute(type=str)
    base_box = Attribute(type=BaseBox)
    flavor = Attribute(type=str)
    block_internet = Attribute(type=bool, default=False)

    def __init__(self, name, base_box, flavor, block_internet):
        self.name = name
        self.base_box = base_box
        self.flavor = flavor
        self.block_internet = block_internet


class HostList(Sequence):
    item_type = Host


class Router(Object):
    name = Attribute(type=str)
    cidr = Attribute(type=str)

    def __init__(self, name, cidr):
        self.name = name
        self.cidr = cidr


class RouterList(Sequence):
    item_type = Router


class Network(Object):
    name = Attribute(type=str)
    cidr = Attribute(type=str)
    accessible_by_user = Attribute(type=bool, default=True)

    def __init__(self, name, cidr, accessible_by_user):
        self.name = name
        self.cidr = cidr
        self.accessible_by_user = accessible_by_user


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
    name = Attribute(type=str)
    nodes = Attribute(type=StrList)

    def __init__(self, name):
        self.name = name
        self.nodes = StrList()

    def add_node(self, node):
        self.nodes.append(node)


class GroupList(Sequence):
    item_type = Group


class TopologyDefinition(Object):
    provider = Attribute(
        type=Typed(
            Provider,
            from_yaml=(lambda loader, node, rtd: Provider[loader.construct_object(node)]),
            to_yaml=(lambda dumper, data, rtd: dumper.represent_data(data.name))
        )
    )

    name = Attribute(type=str)
    hosts = Attribute(type=HostList)
    routers = Attribute(type=RouterList)
    networks = Attribute(type=NetworkList)
    net_mappings = Attribute(type=NetworkMappingList)
    router_mappings = Attribute(type=RouterMappingList)
    groups = Attribute(type=GroupList)

    _hosts_index: dict = None
    _networks_index: dict = None

    def __init__(self, provider, name):
        self.provider = provider
        self.name = name
        self.hosts = HostList()
        self.routers = RouterList()
        self.networks = NetworkList()
        self.net_mappings = NetworkMappingList()
        self.router_mappings = RouterMappingList()
        self.groups = GroupList()

    @staticmethod
    def from_file(file) -> 'TopologyDefinition':
        return TopologyDefinition.load(open(file, mode='r'))

    # TODO -- generalize?
    def find_host_by_name(self, name) -> Host:
        if self._hosts_index is None:
            self._hosts_index = {h.name: h for h in self.hosts}
        return self._hosts_index[name]

    def find_network_by_name(self, name) -> Network:
        if self._networks_index is None:
            self._networks_index = {h.name: h for h in self.networks}
        return self._networks_index[name]

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
