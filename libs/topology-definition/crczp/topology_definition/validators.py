"""
Module for topology definition validators.
"""

import re
from ipaddress import ip_address, ip_network
from itertools import combinations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from yamlize import StrList

    from crczp.topology_definition.models import (
        WAN,
        GroupList,
        MonitoringTargetList,
        Network,
        NetworkList,
        NetworkMappingList,
        RouterMappingList,
        TargetList,
        TopologyDefinition,
        VolumeList,
    )

VALID_NAMES_REGEX = r'^[a-z]([a-z0-9A-Z-])*$'
_UNIQ_MSG = (
    'Uniqueness violation. The following name identifiers are not unique '
    'within the [{}] definition: {}.'
)


class TopologyValidation:
    """
    Class for topology definition validation.
    """

    @staticmethod
    def is_valid_ostack_name(obj: object, name: str) -> None:
        """
        Validate OpenStack name.
        """
        if not re.match(VALID_NAMES_REGEX, name):
            _msg = 'Cannot set {}.name to "{}". It does not match regex "{}".'
            raise ValueError(_msg.format(obj.__class__.__name__, name, VALID_NAMES_REGEX))

    @staticmethod
    def validate_net_mappings(
        obj: 'TopologyDefinition', net_mappings: 'NetworkMappingList'
    ) -> bool:
        """
        Validate network mappings.
        """
        _msg = 'Invalid network mapping with ip "{}". Cannot find {} with name "{}".'
        for net_mapping in net_mappings:
            if not obj.find_host_by_name(net_mapping.host):
                raise ValueError(_msg.format(net_mapping.ip, 'host', net_mapping.host))
            if not obj.find_network_by_name(net_mapping.network):
                raise ValueError(_msg.format(net_mapping.ip, 'network', net_mapping.network))
        return True

    @staticmethod
    def validate_router_mappings(
        obj: 'TopologyDefinition', router_mappings: 'RouterMappingList'
    ) -> bool:
        """
        Validate router mappings.
        """
        TopologyValidation.validate_name_mappings(obj, router_mappings)
        TopologyValidation.validate_cidrs_and_ips(obj, router_mappings)

        return True

    @staticmethod
    def validate_name_mappings(
        obj: 'TopologyDefinition', router_mappings: 'RouterMappingList'
    ) -> None:
        """
        Validate name mappings.
        """
        _msg = 'Invalid router mapping with ip "{}". Cannot find {} with name "{}".'
        for router_mapping in router_mappings:
            if not obj.find_router_by_name(router_mapping.router):
                raise ValueError(_msg.format(router_mapping.ip, 'router', router_mapping.router))
            if not obj.find_network_by_name(router_mapping.network):
                raise ValueError(_msg.format(router_mapping.ip, 'network', router_mapping.network))

    @staticmethod
    def validate_cidrs_and_ips(
        obj: 'TopologyDefinition', router_mappings: 'RouterMappingList'
    ) -> None:
        """
        Validate CIDRs and IP addresses.
        """
        networks = list(obj.networks) + [obj.wan]
        net_mappings = list(obj.net_mappings)
        router_mappings_list = list(router_mappings)

        TopologyValidation.raise_if_overlaps(networks)

        TopologyValidation.raise_if_not_in_network(obj.net_mappings, obj.networks)
        TopologyValidation.raise_if_not_in_network(router_mappings, obj.networks)

        TopologyValidation.raise_if_ip_not_unique(net_mappings + router_mappings_list)

    @staticmethod
    def raise_if_overlaps(networks: list['Network' | 'WAN']) -> None:
        """
        Raise error if networks overlap.
        """
        cidrs = {network.name: ip_network(network.cidr) for network in networks}
        for net_a, net_b in combinations(cidrs, 2):
            if cidrs[net_a].overlaps(cidrs[net_b]):
                _msg = 'Network "{}" overlaps with network "{}".'
                raise ValueError(_msg.format(cidrs[net_a], cidrs[net_b]))

    @staticmethod
    def raise_if_not_in_network(
        mappings: 'NetworkMappingList | RouterMappingList',
        networks: 'NetworkList',
    ) -> None:
        """
        Raise error if IP is not in network.
        """
        mappings_dict = {mapp.network: ip_address(mapp.ip) for mapp in mappings}
        networks_dict = {net.name: ip_network(net.cidr) for net in networks}
        for network, ip in mappings_dict.items():
            if ip not in networks_dict[network]:
                _msg = 'IP address "{}" is not valid host address of "{}" defined in network "{}".'
                raise ValueError(_msg.format(ip, networks_dict[network], network))

    @staticmethod
    def raise_if_ip_not_unique(
        mappings: list['NetworkMappingList | RouterMappingList'],
    ) -> None:
        """
        Raise error if IP is not unique.
        """
        mappings_ip = [mapp.ip for mapp in mappings]
        duplicates_ip = TopologyValidation.get_duplicates(mappings_ip)

        if duplicates_ip:
            _msg = (
                'Uniqueness violation. The IP address of either of mappings '
                'must be unique. Incorrect IP addresses: {}.'
            )
            raise ValueError(_msg.format(duplicates_ip))

    @staticmethod
    def validate_groups(obj: 'TopologyDefinition', groups: 'GroupList') -> bool:
        """
        Validate groups.
        """
        TopologyValidation.raise_if_not_unique('groups', [g.name for g in groups])

        _msg = 'Invalid group with name "{}". Cannot find a node (host or router) with name "{}".'
        for group in groups:
            for node in group.nodes:
                if not obj.find_host_by_name(node) and not obj.find_router_by_name(node):
                    raise ValueError(_msg.format(group.name, node))
        return True

    @staticmethod
    def validate_group_nodes(_obj: object, nodes: 'StrList') -> bool:
        """
        Validate group nodes.
        """
        for node in nodes:
            if not re.match(VALID_NAMES_REGEX, node):
                _msg = 'Invalid name "{}" in Group.nodes. It does not match regex "{}".'
                raise ValueError(_msg.format(node, VALID_NAMES_REGEX))

        TopologyValidation.raise_if_not_unique('Group.nodes', list(nodes))

        return True

    @staticmethod
    def validate_name_uniqueness(obj: 'TopologyDefinition', networks: 'NetworkList') -> bool:
        """
        Validate name uniqueness.
        """
        a = [obj.name]
        b = [h.name for h in obj.hosts]
        c = [r.name for r in obj.routers]
        d = [n.name for n in networks]
        e = [obj.wan.name]

        TopologyValidation.raise_if_not_unique(
            'name, hosts, routers, networks, wan', a + b + c + d + e
        )

        return True

    @staticmethod
    def raise_if_not_unique(what_for: str, elements: list[str]) -> None:
        """
        Raise error if elements are not unique.
        """
        duplicates = TopologyValidation.get_duplicates(elements)
        if duplicates:
            raise ValueError(_UNIQ_MSG.format(what_for, duplicates))

    @staticmethod
    def get_duplicates(elements: list[str]) -> list[str]:
        """
        Get duplicate elements.
        """
        result = set()

        unique_elements = set(elements)

        if len(elements) > len(unique_elements):
            for element in elements:
                if element not in unique_elements:
                    result.add(element)
                else:
                    unique_elements.remove(element)

        return list(result)

    @staticmethod
    def is_volumes_valid(_obj: object, volumes: 'VolumeList') -> None:
        """
        Validate volumes.
        """
        if volumes is not None and len(volumes) < 1:
            raise ValueError('Volumes must contain at least one entry for system disk')

    @staticmethod
    def validate_monitoring_targets(
        obj: 'TopologyDefinition', targets: 'MonitoringTargetList'
    ) -> bool:
        """
        Validate monitoring targets.
        """
        node_names = set(
            [host.name for host in obj.hosts] + [router.name for router in obj.routers]
        )
        used_node_names = set()

        for target in targets:
            if target.node not in node_names:
                _msg = 'Invalid node name in MonitoringTarget.node. No node with name "{}" found.'
                raise ValueError(_msg.format(target.node))

            if target.node in used_node_names:
                _msg = (
                    'Duplicate node name "{}" in MonitoringTarget.node. '
                    'Only define each target once.'
                )
                raise ValueError(_msg.format(target.node))

            used_node_names.add(target.node)

        return True

    @staticmethod
    def validate_targets(_obj: object, targets: 'TargetList') -> bool:
        """
        Validate targets.
        """
        used_ports = set()

        for target in targets:
            if target.port in used_ports:
                _msg = (
                    'Port "{}" in MonitoringTarget.ports is duplicated. '
                    'Only define each port once on a single host.'
                )
                raise ValueError(_msg.format(target.port))

            used_ports.add(target.port)
            if target.port < 1 or target.port > 65535:
                _msg = (
                    'Port "{}" in MonitoringTarget.ports is not a valid port number. '
                    'Port number must be in range <1, 65535>.'
                )
                raise ValueError(_msg.format(target.port))

        return True
