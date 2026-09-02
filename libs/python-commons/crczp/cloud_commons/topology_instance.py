"""Topology instance module for managing topology instances."""

from collections.abc import Iterable
from typing import Optional, cast

import yaml
from crczp.topology_definition.models import (
    DockerContainers,
    Group,
    Host,
    MonitoringTargetHTTP,
    MonitoringTargetICMP,
    MonitoringTargetTCP,
    Network,
    NetworkMappingList,
    Router,
    RouterList,
    RouterMappingList,
    TopologyDefinition,
)
from netaddr import IPNetwork, IPSet
from typing_extensions import override

from crczp.cloud_commons.exceptions import CrczpException, InvalidTopologyDefinition
from crczp.cloud_commons.topology_elements import (
    MAN,
    Link,
    Node,
    NodeToNodeLinkPair,
    SecurityGroups,
)
from crczp.cloud_commons.transformation_configuration import TransformationConfiguration

MAN_NAME = 'man'  # Management Node
NAME_SEPARATOR = '-'
MAN_NET_NAME = MAN_NAME + NAME_SEPARATOR + 'network'


class TopologyInstance:
    """
    Represents a topology instance.
    """

    def __init__(
        self,
        topology_definition: TopologyDefinition,
        trc: TransformationConfiguration,
        containers: Optional[DockerContainers] = None,
    ):
        self.topology_definition = topology_definition
        self.containers = containers

        self.name = None
        self.ip = None

        self.man = MAN(MAN_NAME, trc.man_flavor, trc.man_image, trc.man_user)

        self.man_network = Network(MAN_NET_NAME, trc.sandbox_man_cidr, False, True)
        self.wan = topology_definition.wan

        self.links: dict[str, Link] = {}

        self._create_extra_nodes_links()
        self._create_hosts_links(topology_definition.net_mappings)
        self._create_routers_links(topology_definition.routers, topology_definition.router_mappings)

        # create helper indexes
        self.topology_definition.index()
        self._nodes: dict[str, Node] = {
            **self.topology_definition._hosts_index,
            **self.topology_definition._routers_index,
            MAN_NAME: self.man,
        }
        self._networks: dict[str, Network] = {
            **self.topology_definition._networks_index,
            self.wan.name: self.wan,
            MAN_NET_NAME: self.man_network,
        }
        self._node_links: dict[str, list[Link]] = {node_name: [] for node_name in self._nodes}
        self._network_links: dict[str, list[Link]] = {
            network_name: [] for network_name in self._networks
        }
        for link in self.links.values():
            self._node_links[link.node.name].append(link)
            self._network_links[link.network.name].append(link)

    # get nodes

    def get_hosts(self) -> Iterable[Host]:
        """
        Return an iterable of TI Hosts.
        """
        return cast(Iterable[Host], self.topology_definition.hosts)

    def get_hidden_hosts(self) -> list[Host]:
        """
        Return a list of hidden TI Hosts.
        """
        return [host for host in self.get_hosts() if host.hidden]

    def get_block_internet_hosts(self) -> list[Host]:
        """
        Return a list of TI Hosts that has blocked access to the internet.
        """
        return [host for host in self.get_hosts() if host.block_internet]

    def get_routers(self) -> Iterable[Router]:
        """
        Return an iterable of TI Routers.
        """
        return cast(Iterable[Router], self.topology_definition.routers)

    def get_node(self, name: str) -> Optional[Node]:
        """
        Return a TI virtual machine, or None if there is no machine of that name.

        :param name: The name of a TI virtual machine
        """
        return self._nodes.get(name)

    def get_nodes(self) -> Iterable[Node]:
        """
        Return an iterable of TI virtual machines.
        """
        return self._nodes.values()

    def get_nodes_without_man(self) -> list[Node]:
        """
        Return a list of TI virtual machines without Management Access Node (MAN).
        """
        return [node for node in self.get_nodes() if node is not self.man]

    def get_visible_hosts(self) -> list[Host]:
        """
        Return a list of TI virtual machines that are not hidden directly
        or through their network.
        """
        visible_networks = self.get_visible_networks()
        hosts: list[Host] = []
        for mapping in self.topology_definition.net_mappings:
            node = self.get_node(mapping.host)
            network = self.get_network(mapping.network)
            if isinstance(node, Host) and not node.hidden and network in visible_networks:
                hosts.append(node)

        # removes duplicates caused by hosts assigned to multiple networks
        return list(set(hosts))

    def get_visible_routers(self) -> list[Router]:
        """
        Return a list of TI routers that are not hidden.
        """
        return [router for router in self.topology_definition.routers if not router.hidden]

    # get networks

    def get_hosts_networks(self) -> Iterable[Network]:
        """
        Return an iterable of TI user-defined Networks.
        """
        return cast(Iterable[Network], self.topology_definition.networks)

    def get_monitored_hosts_tcp(self) -> list[MonitoringTargetTCP]:
        """
        Return a list of monitored hosts and their TCP monitored interfaces/ports.
        """
        mt = self.topology_definition.monitoring_targets
        return mt.tcp or [] if mt else []

    def get_monitored_hosts_icmp(self) -> list[MonitoringTargetICMP]:
        """
        Return a list of monitored hosts and their ICMP monitored interfaces/addresses.
        """
        mt = self.topology_definition.monitoring_targets
        return mt.icmp or [] if mt else []

    def get_monitored_hosts_http(self) -> Optional[MonitoringTargetHTTP]:
        """
        Return the HTTP monitoring target configuration (URL list).
        """
        mt = self.topology_definition.monitoring_targets
        return mt.http if mt else None

    def get_user_accessible_hosts_networks(self) -> list[Network]:
        """
        Return a list of TI user-defined Networks that are accessible to a user.
        """
        return [
            host_network
            for host_network in self.get_hosts_networks()
            if host_network.accessible_by_user
        ]

    def get_network(self, name: str) -> Optional[Network]:
        """
        Return a TI virtual network, or None if there is no network of that name.

        :param name: The name of a TI virtual network
        """
        return self._networks.get(name)

    def get_networks(self) -> Iterable[Network]:
        """
        Return an iterable of TI virtual networks.
        """
        return self._networks.values()

    def get_visible_networks(self) -> list[Network]:
        """
        Retrun a list of TI networks that are not hidden, and their router is not hidden.
        """
        visible_routers = self.get_visible_routers()
        networks: list[Network] = []
        for mapping in self.topology_definition.router_mappings:
            if self.get_node(mapping.router) not in visible_routers:
                continue
            network = self.get_network(mapping.network)
            if network is None:
                raise InvalidTopologyDefinition(
                    f'router mapping refers to an unknown network: {mapping.network}'
                )
            if not network.hidden:
                networks.append(network)
        return networks + [self.wan]

    # get links

    def get_node_links(
        self, node: Node, networks: Optional[Iterable[Network]] = None
    ) -> list[Link]:
        """
        Return a list of Links associated with a given node.

        Optionally specify networks for which the links must be also associated.
            Think of it as a filter.
        """
        return [
            link
            for link in self._node_links[node.name]
            if networks is None or link.network in networks
        ]

    def get_network_links(
        self, network: Network, nodes: Optional[Iterable[Node]] = None
    ) -> list[Link]:
        """
        Return a list of Links associated with a given network.

        Optionally specify nodes for which the links must be also associated.
            Think of it as a filter.
        """
        return [
            link
            for link in self._network_links[network.name]
            if nodes is None or link.node in nodes
        ]

    def get_link_between_node_and_network(self, node: Node, network: Network) -> Optional[Link]:
        """
        Return a Link associated with given server and network.
        """
        links = self.get_node_links(node, [network])
        if not links:
            return None
        if len(links) > 1:
            msg = (
                'invalid number of links between server and network, '
                f'there should be exactly 1 link, got: {links}'
            )
            raise CrczpException(msg)
        return links[0]

    def get_network_default_gateway_link(self, network: Network) -> Optional[Link]:
        """
        Return a default gateway Link of the given network.
        """
        if network not in self.get_hosts_networks():
            return None

        links = self.get_network_links(network, self.get_routers())
        if len(links) != 1:
            msg = (
                'invalid number of links between user-defined Network and Router, '
                f'there should be exactly 1 link, got: {links}'
            )
            raise CrczpException(msg)
        return links[0]

    def get_links(self) -> Iterable[Link]:
        """
        Return an iterable of Links.
        """
        return self.links.values()

    def get_links_from_wan_to_routers(self) -> list[Link]:
        """
        Return a list of Links between routers and WAN
        """
        links: list[Link] = []
        for node in self.get_routers():
            link = self.get_link_between_node_and_network(node, self.wan)
            if link is not None:
                links.append(link)
        return links

    def get_links_to_user_accessible_nodes(self) -> list[Link]:
        """
        Return a list of Links between user-accessible networks and its nodes
        """
        accessible_links = []
        for network in self.get_user_accessible_hosts_networks():
            for link in self.get_network_links(network, self.get_hosts()):
                accessible_links.append(link)

            for link in self.get_network_links(network, self.get_routers()):
                accessible_links.append(link)

        return accessible_links

    # get link pairs

    def get_node_to_nodes_link_pairs(
        self,
        node: Node,
        networks: Optional[Iterable[Network]] = None,
        nodes: Optional[list[Node]] = None,
    ) -> list[NodeToNodeLinkPair]:
        """
        Return a list of NodeToNodeLinkPairs starting from a node to all other nodes
            only over networks adjacent to the starting node.

        Optionally filter links by specifying the list of relevant target nodes
            and/or the list of relevant adjacent networks of the starting node.
        """
        node_to_node_links = []
        for first_link in self.get_node_links(node, networks):
            for second_link in self.get_network_links(first_link.network, nodes):
                if second_link.node is not node:
                    node_to_node_links.append(NodeToNodeLinkPair(first_link, second_link))
        return node_to_node_links

    def get_link_pairs_man_to_nodes_over_management_network(self) -> list[NodeToNodeLinkPair]:
        """
        Return a list of NodeToNodeLinkPairs starting from Management Access Node (MAN)
            and ending at the Nodes over management network.

        A list of NodeToNodeLinkPairs MAN <-> management Network <-> Router/Host
        """
        links = self.get_node_to_nodes_link_pairs(self.man, [self.man_network])
        if len(links) != len(self._nodes) - 1:
            msg = (
                'invalid number of link pairs between MAN and all other machines '
                f'over management network, got: {links}'
            )
            raise CrczpException(msg)
        return links

    # get groups

    def get_groups(self) -> Iterable[Group]:
        """
        Return an iterable of Host groups defined in TopologyDefinition.
        """
        return cast(Iterable[Group], self.topology_definition.groups)

    # special and protected methods

    @override
    def __str__(self) -> str:
        http_monitored_hosts = self.get_monitored_hosts_http()
        ret = {
            'hosts': [str(host) for host in self.get_hosts()],
            'routers': [str(router) for router in self.get_routers()],
            'hosts_networks': [str(host_network) for host_network in self.get_hosts_networks()],
            'wan': str(self.wan),
            'man': str(self.man),
            'man_network': str(self.man_network),
            'links': [str(link) for link in self.get_links()],
            'groups': [str(group) for group in self.get_groups()],
            'monitoring_targets_tcp': [
                str(monitored_host) for monitored_host in self.get_monitored_hosts_tcp()
            ],
            'monitoring_targets_icmp': [
                str(monitored_host) for monitored_host in self.get_monitored_hosts_icmp()
            ],
            'monitoring_targets_http': [str(t) for t in http_monitored_hosts.targets]
            if http_monitored_hosts
            else [],
        }
        if self.ip:
            ret['ip'] = self.ip
        return yaml.dump({'TopologyInstance': ret}, width=1000)

    def _create_extra_nodes_links(self) -> None:
        """
        Create Links for an extra virtual machines of a TI.

        This method should be called only once and within the __init__ method.
        """
        self._add_link(self.man, self.man_network, SecurityGroups.SANDBOX_MAN)
        self._add_link(self.man, self.wan, SecurityGroups.SANDBOX_INTERNAL)

    def _create_hosts_links(self, topology_definition_net_mappings: NetworkMappingList) -> None:
        """
        Create Links for Host machines of a TI.

        This method should be called only once and within the __init__ method.
        """
        for host in self.get_hosts():
            self._add_link(host, self.man_network, SecurityGroups.SANDBOX_MAN_INT)

        for network_mapping in topology_definition_net_mappings:
            host = self.topology_definition.find_host_by_name(network_mapping.host)
            if host is None:
                raise InvalidTopologyDefinition(
                    f'network mapping refers to an unknown host: {network_mapping.host}'
                )
            host_network = self.topology_definition.find_network_by_name(network_mapping.network)
            if host_network is None:
                raise InvalidTopologyDefinition(
                    f'network mapping refers to an unknown network: {network_mapping.network}'
                )
            self._add_link(
                host, host_network, SecurityGroups.SANDBOX_INTERNAL, ip=network_mapping.ip
            )

    def _create_routers_links(
        self,
        topology_definition_routers: RouterList,
        topology_definition_router_mappings: RouterMappingList,
    ) -> None:
        """
        Create Links for Router machines of a TI.

        This method should be called only once and within the __init__ method.
        """
        for router in topology_definition_routers:
            self._add_link(router, self.man_network, SecurityGroups.SANDBOX_MAN_INT)
            self._add_link(router, self.wan, SecurityGroups.SANDBOX_INTERNAL)

        for router_mapping in topology_definition_router_mappings:
            router = self.topology_definition.find_router_by_name(router_mapping.router)
            if router is None:
                raise InvalidTopologyDefinition(
                    f'router mapping refers to an unknown router: {router_mapping.router}'
                )
            host_network = self.topology_definition.find_network_by_name(router_mapping.network)
            if host_network is None:
                raise InvalidTopologyDefinition(
                    f'router mapping refers to an unknown network: {router_mapping.network}'
                )
            self._add_link(router, host_network, SecurityGroups.SANDBOX_INTERNAL, router_mapping.ip)
            # dynamic attribute: Network declares no default_gateway, but yamlize Objects
            # carry a __dict__, so the assignment is valid and callers may read it back.
            host_network.default_gateway = router.name  # ty: ignore[unresolved-attribute]

    def _get_free_ip_address(self, network: Network) -> str:
        """
        Return a free IP address from given network.
        """
        ip_network = IPNetwork(network.cidr)
        ip_set = IPSet([network.cidr])

        # remove network IP
        ip_set.remove(ip_network[0])
        # remove broadcast IP
        ip_set.remove(ip_network[-1])
        for link in [link for link in self.links.values() if link.network == network and link.ip]:
            ip_set.remove(link.ip)

        ip_list = list(ip_set)
        # one of first IP addresses in range of straight sequence of IP addresses is taken by DHCP
        # backward iteration so that picking free IP address will not
        #   unnecessarily divide range of straight sequence of IP addresses
        for lower, upper in zip(reversed(ip_list[:-1]), reversed(ip_list)):
            if lower + 1 == upper:
                return str(upper)

        raise CrczpException(f'no free IP address in network {network.name}')

    def _add_link(
        self,
        node: Node,
        network: Network,
        security_group: SecurityGroups,
        ip: Optional[str] = None,
        mac: Optional[str] = None,
    ) -> None:
        """
        Create and add Link amongst TI links.
        """
        name = f'link{NAME_SEPARATOR}{len(self.links) + 1}'
        self.links[name] = Link(name, node, network, security_group, ip, mac)
