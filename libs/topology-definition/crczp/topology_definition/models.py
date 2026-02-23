from enum import Enum
from typing import Any, Optional, Self

from yamlize import Attribute, Dynamic, Map, Object, Sequence, StrList, Typed

from crczp.topology_definition.utils import rename_deprecated_attribute
from crczp.topology_definition.validators import TopologyValidation


class Protocol(Enum):
	SSH = 1
	WINRM = 2

	@classmethod
	def create(cls, val: str) -> Self:
		try:
			return cls[val.upper()]
		except KeyError as exc:
			raise ValueError(f'Invalid value for Protocol: {val}') from exc


class BaseBox(Object):  # type: ignore[misc]
	image = Attribute(type=str)
	mgmt_user = Attribute(type=str, default='debian')
	mgmt_protocol = Attribute(
		type=Typed(
			Protocol,
			from_yaml=(lambda loader, node, rtd: Protocol.create(loader.construct_object(node))),
			to_yaml=(lambda dumper, data, rtd: dumper.represent_data(data.name)),
		),
		default=Protocol.SSH,
	)

	@classmethod
	def from_yaml(cls, loader: Any, node: Any, _rtd: Any = None) -> 'BaseBox':
		rename_deprecated_attribute(node.value, 'man_user', 'mgmt_user')
		rename_deprecated_attribute(node.value, 'mng_protocol', 'mgmt_protocol')
		return super().from_yaml(loader, node, _rtd)  # type: ignore[no-any-return]


class ExtraValues(Map):  # type: ignore[misc]
	key_type = Typed(str)
	value_type = Dynamic


class Volume(Object):  # type: ignore[misc]
	size = Attribute(type=int, default=None)


class VolumeList(Sequence):  # type: ignore[misc]
	item_type = Volume


class Host(Object):  # type: ignore[misc]
	name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
	base_box = Attribute(type=BaseBox)
	flavor = Attribute(type=str)
	block_internet = Attribute(type=bool, default=False)
	hidden = Attribute(type=bool, default=False)
	extra = Attribute(type=ExtraValues, default=None)
	volumes = Attribute(type=VolumeList, default=None, validator=TopologyValidation.is_volumes_valid)

	def __init__(
		self,
		name: str,
		base_box: BaseBox,
		flavor: str,
		block_internet: bool,
		hidden: bool,
		volumes: Optional[VolumeList],
	) -> None:
		self.name = name
		self.base_box = base_box
		self.flavor = flavor
		self.block_internet = block_internet
		self.hidden = hidden
		self.volumes = volumes


class HostList(Sequence):  # type: ignore[misc]
	item_type = Host


class Router(Object):  # type: ignore[misc]
	name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
	base_box = Attribute(type=BaseBox)
	flavor = Attribute(type=str)
	extra = Attribute(type=ExtraValues, default=None)
	hidden = Attribute(type=bool, default=False)

	def __init__(self, name: str, base_box: BaseBox, flavor: str) -> None:
		self.name = name
		self.base_box = base_box
		self.flavor = flavor


class RouterList(Sequence):  # type: ignore[misc]
	item_type = Router


class Network(Object):  # type: ignore[misc]
	name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
	cidr = Attribute(type=str)
	accessible_by_user = Attribute(type=bool, default=True)
	hidden = Attribute(type=bool, default=False)

	def __init__(self, name: str, cidr: str, accessible_by_user: bool, hidden: bool) -> None:
		self.name = name
		self.cidr = cidr
		self.accessible_by_user = accessible_by_user
		self.hidden = hidden


class WAN(Object):  # type: ignore[misc]
	name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
	cidr = Attribute(type=str)

	def __init__(self, name: str, cidr: str) -> None:
		self.name = name
		self.cidr = cidr


class NetworkList(Sequence):  # type: ignore[misc]
	item_type = Network


class NetworkMapping(Object):  # type: ignore[misc]
	host = Attribute(type=str)
	network = Attribute(type=str)
	ip = Attribute(type=str)

	def __init__(self, host: str, network: str, ip: str) -> None:
		self.host = host
		self.network = network
		self.ip = ip


class NetworkMappingList(Sequence):  # type: ignore[misc]
	item_type = NetworkMapping


class RouterMapping(Object):  # type: ignore[misc]
	router = Attribute(type=str)
	network = Attribute(type=str)
	ip = Attribute(type=str)

	def __init__(self, router: str, network: str, ip: str) -> None:
		self.router = router
		self.network = network
		self.ip = ip


class RouterMappingList(Sequence):  # type: ignore[misc]
	item_type = RouterMapping


class Group(Object):  # type: ignore[misc]
	name = Attribute(type=str, validator=TopologyValidation.is_valid_ostack_name)
	nodes = Attribute(type=StrList, validator=TopologyValidation.validate_group_nodes)

	def __init__(self, name: str) -> None:
		self.name = name
		self.nodes = StrList()

	def add_node(self, node: str) -> None:
		self.nodes.append(node)


class GroupList(Sequence):  # type: ignore[misc]
	item_type = Group


class Target(Object):  # type: ignore[misc]
	interface = Attribute(type=str)
	port = Attribute(type=int)


class TargetList(Sequence):  # type: ignore[misc]
	item_type = Target


class MonitoringTarget(Object):  # type: ignore[misc]
	node = Attribute(type=str)
	targets = Attribute(type=TargetList, validator=TopologyValidation.validate_targets)


class MonitoringTargetList(Sequence):  # type: ignore[misc]
	item_type = MonitoringTarget


class TopologyDefinition(Object):  # type: ignore[misc]
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
	monitoring_targets = Attribute(
		type=MonitoringTargetList, validator=TopologyValidation.validate_monitoring_targets, default=None
	)

	_indexed: bool = False
	_hosts_index: dict[str, Host] = {}
	_routers_index: dict[str, Router] = {}
	_networks_index: dict[str, Network] = {}

	def __init__(self, name: str, wan: WAN) -> None:
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
	def from_file(file: str) -> 'TopologyDefinition':
		with open(file) as f:
			return TopologyDefinition.load(f)  # type: ignore[no-any-return]

	def index(self) -> None:
		self._hosts_index = {h.name: h for h in self.hosts}
		self._routers_index = {r.name: r for r in self.routers}
		self._networks_index = {n.name: n for n in self.networks}
		TopologyDefinition._indexed = True

	def find_host_by_name(self, name: str) -> Optional[Host]:
		if not self._indexed:
			self.index()
		return self._hosts_index.get(name, None)

	def find_router_by_name(self, name: str) -> Optional[Router]:
		if not self._indexed:
			self.index()
		return self._routers_index.get(name, None)

	def find_network_by_name(self, name: str) -> Optional[Network]:
		if not self._indexed:
			self.index()
		return self._networks_index.get(name, None)

	def add_host(self, host: Host) -> None:
		self.hosts.append(host)

	def add_router(self, router: Router) -> None:
		self.routers.append(router)

	def add_net_mapping(self, net_mapping: NetworkMapping) -> None:
		self.net_mappings.append(net_mapping)

	def add_router_mappings(self, router_mapping: RouterMapping) -> None:
		self.router_mappings.append(router_mapping)

	def add_group(self, group: Group) -> None:
		self.groups.append(group)


class Container(Object):  # type: ignore[misc]
	name = Attribute(type=str)
	image = Attribute(type=str, default='')
	dockerfile = Attribute(type=str, default='')


class ContainerList(Sequence):  # type: ignore[misc]
	item_type = Container


class ContainerMapping(Object):  # type: ignore[misc]
	container = Attribute(type=str)
	host = Attribute(type=str)
	port = Attribute(type=int)
	hidden = Attribute(type=bool, default=False)


class ContainerMappingList(Sequence):  # type: ignore[misc]
	item_type = ContainerMapping


class DockerContainers(Object):  # type: ignore[misc]
	containers = Attribute(type=ContainerList)
	container_mappings = Attribute(type=ContainerMappingList)
	hide_all = Attribute(type=bool, default=False)

	@staticmethod
	def from_file(file: str) -> 'DockerContainers':
		with open(file) as f:
			return DockerContainers.load(f)  # type: ignore[no-any-return]
