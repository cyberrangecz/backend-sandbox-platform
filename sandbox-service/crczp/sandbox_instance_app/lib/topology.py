"""Topology representation classes for sandbox visualization."""

from typing import Any

import structlog

from crczp.cloud_commons import UsersRoles
from crczp.sandbox_common_lib.common_cloud import list_images
from crczp.sandbox_instance_app.lib.nodes import find_image_for_node, get_node_image_has_gui_access

LOG = structlog.getLogger()


class Topology:  # pylint: disable=too-few-public-methods
    """Represents a topology of a sandbox."""

    class HostNode:  # pylint: disable=too-few-public-methods
        """Represents a host node in the topology."""

        def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
            self,
            name: str,
            os_type: str | None,
            gui_access: bool,
            is_accessible: Any,
            ip: Any,
            visible_by_roles: list[str] | None,
        ) -> None:
            """
            Initialize a HostNode instance.

            :param str name: The name of the host
            :param os_type: The operating system type, None if the image does not report one
            :param bool gui_access: Whether GUI access is available
            :param str ip: The IP address of the host
            :param visible_by_roles: The host's own declared visibility roles, None if boolean
            """
            self.name = name
            self.os_type = os_type
            self.gui_access = gui_access
            self.is_accessible = is_accessible
            self.ip = ip
            self.visible_by_roles = visible_by_roles

    class RouterNode(HostNode):  # pylint: disable=too-few-public-methods
        """Represents a router node in the topology."""

        def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
            self,
            name: str,
            os_type: str | None,
            gui_access: bool,
            subnets: list['Topology.Subnet'],
            is_accessible: Any,
            ip: Any,
            visible_by_roles: list[str] | None,
        ) -> None:
            """
            Initialize a RouterNode instance.

            :param str name: The name of the router
            :param os_type: The operating system type, None if the image does not report one
            :param bool gui_access: Whether GUI access is available
            :param subnets: List of subnets connected to this router
            :type subnets: List[Topology.Subnet]
            :param str ip: The IP address of the router
            :param visible_by_roles: The router's own declared visibility roles, None if boolean
            """
            super().__init__(name, os_type, gui_access, is_accessible, ip, visible_by_roles)
            self.subnets = subnets

    class Subnet:  # pylint: disable=too-few-public-methods
        """Represents a subnet in the topology."""

        def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
            self,
            name: str,
            cidr: str,
            hosts: list['Topology.HostNode'],
            accessible_by_roles: list[str] | None,
            visible_by_roles: list[str] | None,
        ) -> None:
            """
            Initialize a Subnet instance.

            :param str name: The name of the subnet
            :param str cidr: The subnet CIDR mask
            :param hosts: List of hosts in this subnet
            :type hosts: List[Topology.HostNode]
            :param accessible_by_roles: The network's own declared reach roles, None if boolean
            :param visible_by_roles: The network's own declared visibility roles, None if boolean
            """
            self.name = name
            self.cidr = cidr
            self.hosts = hosts
            self.accessible_by_roles = accessible_by_roles
            self.visible_by_roles = visible_by_roles

    def __init__(self, top_inst: Any, users_roles: UsersRoles) -> None:
        """
        Initialize a Topology instance.

        :param TopologyInstance top_inst: The topology instance to build from
        :param users_roles: The requesting user's roles
        """
        self.routers: list[Topology.RouterNode] = []
        self._build_topology(top_inst, users_roles)

    def _build_topology(self, top_inst: Any, users_roles: UsersRoles) -> None:
        """
        Build the complete topology structure from TopologyInstance.

        :param TopologyInstance top_inst: The topology instance to build from
        :param users_roles: The requesting user's roles
        """
        images = list_images()
        subnets_dict = self._create_subnets_with_hosts(top_inst, users_roles, images)
        self._create_routers_with_subnets(top_inst, users_roles, images, subnets_dict)

    def _create_subnets_with_hosts(
        self, top_inst: Any, users_roles: UsersRoles, images: Any
    ) -> dict[str, 'Topology.Subnet']:
        """
        Create all subnets and populate them with hosts.

        :param TopologyInstance top_inst: The topology instance
        :param users_roles: The requesting user's roles
        :param images: List of available images
        :type images: list
        :return: Dictionary mapping subnet names to subnet objects
        :rtype: dict[str, Topology.Subnet]
        """
        subnets_dict = {}

        for network in top_inst.get_visible_networks(users_roles):
            if self._is_wan_network(network):
                continue

            hosts_in_network = self._get_hosts_for_network(network, top_inst, users_roles, images)
            subnet = self.Subnet(
                name=network.name,
                cidr=network.cidr,
                hosts=hosts_in_network,
                # network may be the WAN pseudo-network here when _is_wan_network fails to
                # recognize it by name; the WAN declares neither field.
                accessible_by_roles=getattr(network, 'accessible_by_roles', None),
                visible_by_roles=getattr(network, 'visible_by_roles', None),
            )
            subnets_dict[network.name] = subnet

        return subnets_dict

    def _create_routers_with_subnets(
        self,
        top_inst: Any,
        users_roles: UsersRoles,
        images: Any,
        subnets_dict: dict[str, 'Topology.Subnet'],
    ) -> None:
        """
        Create routers and assign their connected subnets.

        :param TopologyInstance top_inst: The topology instance
        :param users_roles: The requesting user's roles
        :param images: List of available images
        :type images: list
        :param subnets_dict: Dictionary mapping subnet names to subnet objects
        :type subnets_dict: dict[str, Topology.Subnet]
        """
        for router_node in top_inst.get_visible_routers(users_roles):
            router_image = find_image_for_node(router_node, images)
            if router_image is None:
                continue

            router_subnets = self._get_subnets_for_router(router_node, top_inst, subnets_dict)

            wan_link = top_inst.get_link_between_node_and_network(router_node, top_inst.wan)
            wan_ip = wan_link.ip if wan_link else None

            router = self.RouterNode(
                name=router_node.name,
                os_type=router_image.os_type,
                gui_access=get_node_image_has_gui_access(router_image),
                subnets=router_subnets,
                is_accessible=True,
                ip=wan_ip,
                visible_by_roles=router_node.visible_by_roles,
            )
            self.routers.append(router)

    def _is_wan_network(self, network: Any) -> bool:
        """
        Check if network is a WAN network that should be ignored.

        :param network: The network object to check
        :return: True if network is WAN, False otherwise
        :rtype: bool
        """
        return network.name.lower() == 'wan'

    def _get_hosts_for_network(
        self, network: Any, top_inst: Any, users_roles: UsersRoles, images: Any
    ) -> list['Topology.HostNode']:
        """
        Get all hosts connected to a specific network.

        :param network: The network object
        :param TopologyInstance top_inst: The topology instance
        :param users_roles: The requesting user's roles
        :param images: List of available images
        :type images: list
        :return: List of host nodes in the network
        :rtype: list[Topology.HostNode]
        """
        hosts_in_network = []

        for link in top_inst.get_network_links(network, top_inst.get_visible_hosts(users_roles)):
            host_node = link.node
            host_image = find_image_for_node(host_node, images)

            if host_image is None:
                continue

            host = self.HostNode(
                name=host_node.name,
                os_type=host_image.os_type,
                gui_access=get_node_image_has_gui_access(host_image),
                is_accessible=top_inst.network_has_reach(network, users_roles),
                ip=link.ip,
                visible_by_roles=host_node.visible_by_roles,
            )
            hosts_in_network.append(host)

        return hosts_in_network

    def _get_subnets_for_router(
        self, router_node: Any, top_inst: Any, subnets_dict: dict[str, 'Topology.Subnet']
    ) -> list['Topology.Subnet']:
        """
        Get all subnets connected to a specific router.

        :param router_node: The router node object
        :param TopologyInstance top_inst: The topology instance
        :param subnets_dict: Dictionary mapping subnet names to subnet objects
        :type subnets_dict: dict[str, Topology.Subnet]
        :return: List of subnets connected to the router
        :rtype: list[Topology.Subnet]
        """
        router_subnets = []

        for link in top_inst.get_node_links(router_node, top_inst.get_hosts_networks()):
            if self._is_wan_network(link.network):
                continue

            if link.network.name in subnets_dict:
                router_subnets.append(subnets_dict[link.network.name])

        return router_subnets

    def get_hosts(self) -> list['Topology.HostNode']:
        """
        Get all hosts from all routers' subnets.

        :return: List of all host nodes in the topology
        :rtype: list[Topology.HostNode]
        """
        all_hosts = []
        for router in self.routers:
            for subnet in router.subnets:
                all_hosts.extend(subnet.hosts)
        return all_hosts
