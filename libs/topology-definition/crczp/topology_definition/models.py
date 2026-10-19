"""
Module for topology definition models.
"""

from collections.abc import Callable
from enum import Enum, StrEnum
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    PlainSerializer,
    PrivateAttr,
    field_validator,
)
from ruamel.yaml.nodes import MappingNode, ScalarNode

from crczp.topology_definition.utils import (
    reject_key_conflict,
    reject_null_value,
    rename_deprecated_attribute,
)
from crczp.topology_definition.validators import TopologyValidation
from crczp.topology_definition.yaml_model import (
    NULL_TAG,
    STR_TAG,
    YamlBool,
    YamlInt,
    YamlModel,
    YamlModelError,
    YamlStr,
    positional_init,
    value_node,
)


def _checked_by(check: Callable[[object, Any], object]) -> AfterValidator:
    """
    Validate a field's value with a TopologyValidation check, which raises ValueError if invalid.
    """

    def validate(value: Any) -> Any:
        check(None, value)
        return value

    return AfterValidator(validate)


class ForwardingDirection(StrEnum):
    """
    Enum for the traffic direction mirrored by a network-forwarding rule.
    """

    IN = 'in'
    OUT = 'out'
    BOTH = 'both'


def _forwarding_direction(value: Any) -> ForwardingDirection:
    if isinstance(value, ForwardingDirection):
        return value
    try:
        return ForwardingDirection(value)
    except ValueError as exc:
        raise ValueError(
            f'network_forwarding has invalid direction "{value}". '
            f'Must be one of {sorted(d.value for d in ForwardingDirection)}.'
        ) from exc


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
        except (KeyError, AttributeError) as exc:
            raise ValueError(f'Invalid value for Protocol: {val}') from exc


def _protocol(value: Any) -> Protocol:
    return value if isinstance(value, Protocol) else Protocol.create(value)


StrList = list[YamlStr]
ExtraValues = dict[YamlStr, Any]


class NamedModel(YamlModel):
    """
    A model with a name usable as an OpenStack resource name.
    """

    name: YamlStr

    @field_validator('name')
    @classmethod
    def _valid_name(cls, name: str) -> str:
        TopologyValidation.is_valid_ostack_name(cls, name)
        return name


class BaseBox(YamlModel):
    """
    Base box definition.
    """

    image: YamlStr
    mgmt_user: YamlStr = 'debian'
    mgmt_protocol: Annotated[
        Protocol,
        BeforeValidator(_protocol),
        PlainSerializer(lambda protocol: protocol.name, when_used='json'),
    ] = Protocol.SSH
    # SSH password for images that cannot receive the injected management key;
    # the key is still tried first.
    mgmt_password: YamlStr | None = None

    @positional_init
    def __init__(self, image: str = '', **data: Any) -> None:
        # The MAN builds its base box empty and assigns the image afterwards.
        fields: dict[str, Any] = {'image': image, **data}
        super().__init__(**fields)

    @classmethod
    def _fix_node(cls, node: MappingNode) -> None:
        rename_deprecated_attribute(node.value, 'man_user', 'mgmt_user')
        rename_deprecated_attribute(node.value, 'mng_protocol', 'mgmt_protocol')
        for index, (key, value) in enumerate(node.value):
            if key.value != 'mgmt_password' or not isinstance(value, ScalarNode):
                continue
            if value.tag != NULL_TAG:
                # An unquoted 1234 would resolve to int and fail the str check cryptically.
                # The node is copied because an anchor may share it with other keys.
                node.value[index] = (
                    key,
                    ScalarNode(
                        STR_TAG, value.value, value.start_mark, value.end_mark, style=value.style
                    ),
                )

    def _check_loaded(self, node: MappingNode) -> None:
        if self.mgmt_password is not None and not self.mgmt_password:
            raise YamlModelError(
                'mgmt_password must not be empty.', value_node(node, 'mgmt_password')
            )
        if self.mgmt_password is not None and self.mgmt_protocol != Protocol.SSH:
            raise YamlModelError('mgmt_password is supported only with mgmt_protocol ssh.', node)


class Volume(YamlModel):
    """
    Disk of a host. volumes[0] is the system disk, created from base_box.image and sized by
    size (GB); later entries are extra disks, blank or created from image (a Glance image
    name on OpenStack, an EBS snapshot id on AWS).
    """

    size: YamlInt
    image: YamlStr | None = None


VolumeList = list[Volume]


class Host(NamedModel):
    """
    Host definition.
    """

    base_box: BaseBox
    flavor: YamlStr
    block_internet: YamlBool = False
    hidden: YamlBool = False
    # When False, the host is deployed but stage one never configures it (networking, hostname,
    # user access, docker, NetBird, exporters); the definition's own playbook may. Routers have
    # no such switch: they carry the sandbox routing.
    managed: YamlBool = True
    visible_by_roles: StrList | None = None
    extra: ExtraValues | None = None
    volumes: Annotated[VolumeList | None, _checked_by(TopologyValidation.is_volumes_valid)] = None

    @positional_init
    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        name: str,
        base_box: BaseBox,
        flavor: str,
        block_internet: bool,
        hidden: bool,
        volumes: VolumeList | None,
        **data: Any,
    ) -> None:
        fields: dict[str, Any] = {
            'name': name,
            'base_box': base_box,
            'flavor': flavor,
            'block_internet': block_internet,
            'hidden': hidden,
            'volumes': volumes,
            **data,
        }
        super().__init__(**fields)

    @field_validator('visible_by_roles')
    @classmethod
    def _valid_visible_by_roles(cls, roles: list[str] | None) -> list[str] | None:
        TopologyValidation.validate_visible_by_roles(cls, roles)
        return roles

    @classmethod
    def _fix_node(cls, node: MappingNode) -> None:
        reject_key_conflict(node.value, 'hidden', 'visible_by_roles')
        reject_null_value(node.value, 'visible_by_roles')


HostList = list[Host]


class Router(NamedModel):
    """
    Router definition.
    """

    base_box: BaseBox
    flavor: YamlStr
    extra: ExtraValues | None = None
    hidden: YamlBool = False
    visible_by_roles: StrList | None = None

    @positional_init
    def __init__(self, name: str, base_box: BaseBox, flavor: str, **data: Any) -> None:
        fields: dict[str, Any] = {'name': name, 'base_box': base_box, 'flavor': flavor, **data}
        super().__init__(**fields)

    @field_validator('visible_by_roles')
    @classmethod
    def _valid_visible_by_roles(cls, roles: list[str] | None) -> list[str] | None:
        TopologyValidation.validate_visible_by_roles(cls, roles)
        return roles

    @classmethod
    def _fix_node(cls, node: MappingNode) -> None:
        reject_key_conflict(node.value, 'hidden', 'visible_by_roles')
        reject_null_value(node.value, 'visible_by_roles')


RouterList = list[Router]


class NamedNetwork(NamedModel):
    """
    A named IPv4 network.
    """

    cidr: YamlStr

    @field_validator('cidr')
    @classmethod
    def _valid_cidr(cls, cidr: str) -> str:
        TopologyValidation.is_ipv4_cidr(cls, cidr)
        return cidr


class Network(NamedNetwork):
    """
    Network definition.
    """

    accessible_by_user: YamlBool = True
    accessible_by_roles: StrList | None = None
    hidden: YamlBool = False
    visible_by_roles: StrList | None = None

    @positional_init
    def __init__(
        self, name: str, cidr: str, accessible_by_user: bool, hidden: bool, **data: Any
    ) -> None:
        fields: dict[str, Any] = {
            'name': name,
            'cidr': cidr,
            'accessible_by_user': accessible_by_user,
            'hidden': hidden,
            **data,
        }
        super().__init__(**fields)

    @field_validator('accessible_by_roles')
    @classmethod
    def _valid_accessible_by_roles(cls, roles: list[str] | None) -> list[str] | None:
        TopologyValidation.validate_accessible_by_roles(cls, roles)
        return roles

    @field_validator('visible_by_roles')
    @classmethod
    def _valid_visible_by_roles(cls, roles: list[str] | None) -> list[str] | None:
        TopologyValidation.validate_visible_by_roles(cls, roles)
        return roles

    @classmethod
    def _fix_node(cls, node: MappingNode) -> None:
        reject_key_conflict(node.value, 'hidden', 'visible_by_roles')
        reject_key_conflict(node.value, 'accessible_by_user', 'accessible_by_roles')
        reject_null_value(node.value, 'visible_by_roles')
        reject_null_value(node.value, 'accessible_by_roles')


class WAN(NamedNetwork):
    """
    WAN definition.
    """

    @positional_init
    def __init__(self, name: str, cidr: str, **data: Any) -> None:
        fields: dict[str, Any] = {'name': name, 'cidr': cidr, **data}
        super().__init__(**fields)


NetworkList = list[Network]


class NetworkMapping(YamlModel):
    """
    Network mapping definition.
    """

    host: YamlStr
    network: YamlStr
    ip: YamlStr

    @positional_init
    def __init__(self, host: str, network: str, ip: str, **data: Any) -> None:
        fields: dict[str, Any] = {'host': host, 'network': network, 'ip': ip, **data}
        super().__init__(**fields)

    @field_validator('ip')
    @classmethod
    def _valid_ip(cls, ip: str) -> str:
        TopologyValidation.is_ipv4_address(cls, ip)
        return ip


NetworkMappingList = list[NetworkMapping]


class RouterMapping(YamlModel):
    """
    Router mapping definition.
    """

    router: YamlStr
    network: YamlStr
    ip: YamlStr

    @positional_init
    def __init__(self, router: str, network: str, ip: str, **data: Any) -> None:
        fields: dict[str, Any] = {'router': router, 'network': network, 'ip': ip, **data}
        super().__init__(**fields)

    @field_validator('ip')
    @classmethod
    def _valid_ip(cls, ip: str) -> str:
        TopologyValidation.is_ipv4_address(cls, ip)
        return ip


RouterMappingList = list[RouterMapping]


class Group(NamedModel):
    """
    Group definition.
    """

    nodes: Annotated[StrList, _checked_by(TopologyValidation.validate_group_nodes)]

    @positional_init
    def __init__(self, name: str, **data: Any) -> None:
        fields: dict[str, Any] = {'name': name, 'nodes': [], **data}
        super().__init__(**fields)

    def add_node(self, node: str) -> None:
        """
        Add node to group.
        """
        self.nodes.append(node)


GroupList = list[Group]


class TargetTCP(YamlModel):
    """
    TCP monitoring target definition. Exactly one of interface or address must be specified.
    """

    interface: YamlStr | None = None
    address: YamlStr | None = None
    port: YamlInt


TargetTCPList = list[TargetTCP]


class MonitoringTargetTCP(YamlModel):
    """
    TCP monitoring target node definition.
    """

    node: YamlStr
    targets: Annotated[TargetTCPList, _checked_by(TopologyValidation.validate_targets_tcp)]


MonitoringTargetTCPList = list[MonitoringTargetTCP]


class TargetICMP(YamlModel):
    """
    ICMP monitoring target definition. Exactly one of interface or address must be specified.
    """

    interface: YamlStr | None = None
    address: YamlStr | None = None


TargetICMPList = list[TargetICMP]


class MonitoringTargetICMP(YamlModel):
    """
    ICMP monitoring target node definition.
    """

    node: YamlStr
    targets: Annotated[TargetICMPList, _checked_by(TopologyValidation.validate_targets_icmp)]


MonitoringTargetICMPList = list[MonitoringTargetICMP]


class TargetHTTP(YamlModel):
    """
    HTTP monitoring target definition.
    """

    url: YamlStr
    check_string: YamlStr | None = None


TargetHTTPList = list[TargetHTTP]


class MonitoringTargetHTTP(YamlModel):
    """
    HTTP monitoring targets (not bound to a specific node).
    """

    targets: Annotated[TargetHTTPList, _checked_by(TopologyValidation.validate_targets_http)]


class MonitoringTargets(YamlModel):
    """
    Consolidated monitoring targets definition.
    """

    tcp: Annotated[
        MonitoringTargetTCPList | None,
        _checked_by(TopologyValidation.validate_monitoring_targets_tcp),
    ] = None
    icmp: Annotated[
        MonitoringTargetICMPList | None,
        _checked_by(TopologyValidation.validate_monitoring_targets_icmp),
    ] = None
    http: MonitoringTargetHTTP | None = None


class VpnEntrypoint(YamlModel):
    """
    VPN entrypoint definition. Declares a topology host or router that acts as a
    Netbird VPN gateway.
    """

    name: Annotated[YamlStr, _checked_by(TopologyValidation.validate_vpn_entrypoint_name)]
    routes: Annotated[StrList, _checked_by(TopologyValidation.validate_vpn_routes)]
    accessible_by_roles: StrList | None = None

    @field_validator('accessible_by_roles')
    @classmethod
    def _valid_accessible_by_roles(cls, roles: list[str] | None) -> list[str] | None:
        TopologyValidation.validate_accessible_by_roles(cls, roles)
        return roles

    @classmethod
    def _fix_node(cls, node: MappingNode) -> None:
        reject_null_value(node.value, 'accessible_by_roles')


VpnEntrypointList = list[VpnEntrypoint]


class VpnDns(YamlModel):
    """
    VPN DNS settings distributed to every client in the sandbox access group.

    ``servers`` is the (non-empty) list of nameserver IPs the clients should use;
    ``search_domains`` is an optional list of DNS search/match domains.
    """

    servers: Annotated[StrList, _checked_by(TopologyValidation.validate_vpn_dns_servers)]
    search_domains: Annotated[
        StrList | None, _checked_by(TopologyValidation.validate_vpn_dns_search_domains)
    ] = None


class Vpn(YamlModel):
    """
    VPN settings for the sandbox (Netbird).

    ``entrypoints`` declares the hosts/routers acting as VPN gateways; ``dns`` is
    an optional DNS configuration applied to the shared access group, i.e. handed
    out to every VPN client of the sandbox.
    """

    entrypoints: VpnEntrypointList | None = None
    dns: VpnDns | None = None


class ForwardingInterface(YamlModel):
    """
    A single interface in a network-forwarding rule, identified by a host or router and
    the network it is attached to. Maps 1:1 to a Neutron port / AWS network interface.
    """

    node: YamlStr
    network: YamlStr


ForwardingInterfaceList = list[ForwardingInterface]


class NetworkForwardingRule(YamlModel):
    """
    Network traffic forwarding (port mirroring) rule. A copy of the traffic on one
    or more source interfaces is delivered to a single destination interface.

    A topology declares at most one rule, so the resources it renders are named after
    the sandbox prefix alone and need no name of their own.

    ``direction`` selects which traffic is mirrored relative to the source
    (``in``/``out``/``both``). Cloud-specific constraints are enforced by the cloud drivers.
    """

    sources: ForwardingInterfaceList
    destination: ForwardingInterface
    direction: Annotated[ForwardingDirection, BeforeValidator(_forwarding_direction)] = (
        ForwardingDirection.BOTH
    )


class TopologyDefinition(NamedModel):  # pylint: disable=too-many-instance-attributes
    """
    Topology definition.
    """

    hosts: HostList
    routers: RouterList
    wan: WAN = Field(default_factory=lambda: WAN('wan', '100.100.100.0/24'))
    networks: NetworkList
    net_mappings: NetworkMappingList
    router_mappings: RouterMappingList
    groups: GroupList
    monitoring_targets: MonitoringTargets | None = None
    vpn: Vpn | None = None
    network_forwarding: NetworkForwardingRule | None = None

    _indexed: bool = PrivateAttr(default=False)
    _hosts_index: dict[str, Host] = PrivateAttr(default_factory=dict)
    _routers_index: dict[str, Router] = PrivateAttr(default_factory=dict)
    _networks_index: dict[str, Network] = PrivateAttr(default_factory=dict)

    @positional_init
    def __init__(self, name: str, wan: WAN, **data: Any) -> None:
        fields: dict[str, Any] = {
            'name': name,
            'wan': wan,
            'hosts': [],
            'routers': [],
            'networks': [],
            'net_mappings': [],
            'router_mappings': [],
            'groups': [],
            **data,
        }
        super().__init__(**fields)

    def _check_loaded(self, node: MappingNode) -> None:
        """
        Run the checks that span several attributes, once the whole document is loaded.

        Each error points at the checked key.
        """
        # Names first: the later checks look nodes and networks up by name.
        cross_attribute_checks = (
            ('networks', TopologyValidation.validate_name_uniqueness),
            ('networks', TopologyValidation.validate_network_cidrs),
            ('net_mappings', TopologyValidation.validate_net_mappings),
            ('router_mappings', TopologyValidation.validate_router_mappings),
            ('groups', TopologyValidation.validate_groups),
            ('monitoring_targets', TopologyValidation.validate_monitoring_targets),
            ('vpn', TopologyValidation.validate_vpn),
            ('network_forwarding', TopologyValidation.validate_network_forwarding),
        )
        for key, validate in cross_attribute_checks:
            try:
                validate(self, getattr(self, key))
            except ValueError as exc:
                raise YamlModelError(str(exc), value_node(node, key)) from exc

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
        if not self._indexed:
            self.index()
        return self._hosts_index.get(name, None)

    def find_router_by_name(self, name: str) -> Router | None:
        """
        Find router by name.
        """
        if not self._indexed:
            self.index()
        return self._routers_index.get(name, None)

    def find_network_by_name(self, name: str) -> Network | None:
        """
        Find network by name.
        """
        if not self._indexed:
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

    def get_declared_roles(self) -> set[str]:
        """
        Return every role name declared anywhere in the definition.
        """
        roles: set[str] = set()
        for host in self.hosts:
            roles.update(host.visible_by_roles or ())
        for router in self.routers:
            roles.update(router.visible_by_roles or ())
        for network in self.networks:
            roles.update(network.visible_by_roles or ())
            roles.update(network.accessible_by_roles or ())
        if self.vpn is not None:
            for entrypoint in self.vpn.entrypoints or ():
                roles.update(entrypoint.accessible_by_roles or ())
        return roles


class Container(YamlModel):
    """
    Container definition.
    """

    name: YamlStr
    image: YamlStr = ''
    dockerfile: YamlStr = ''


ContainerList = list[Container]


class ContainerMapping(YamlModel):
    """
    Container mapping definition.
    """

    container: YamlStr
    host: YamlStr
    port: YamlInt
    hidden: YamlBool = False


ContainerMappingList = list[ContainerMapping]


class DockerContainers(YamlModel):
    """
    Docker containers definition.
    """

    containers: ContainerList
    container_mappings: ContainerMappingList
    hide_all: YamlBool = False
