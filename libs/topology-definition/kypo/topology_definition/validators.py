import re
from ipaddress import ip_address, ip_network, collapse_addresses
from itertools import combinations, product
from typing import List

VALID_NAMES_REGEX = r'^[a-z]([a-z0-9A-Z-])*$'
_UNIQ_MSG = 'Uniqueness violation. The following name identifiers are not unique within the [{}] definition: {}.'


class TopologyValidation:
    @staticmethod
    def is_valid_ostack_name(obj, name: str):
        if not re.match(VALID_NAMES_REGEX, name):
            _msg = 'Cannot set {}.name to "{}". It does not match regex "{}".'
            raise ValueError(_msg.format(obj.__class__.__name__, name, VALID_NAMES_REGEX))

    @staticmethod
    def validate_net_mappings(obj, net_mappings):
        _msg = 'Invalid network mapping with ip "{}". Cannot find {} with name "{}".'
        for net_mapping in net_mappings:
            if not obj.find_host_by_name(net_mapping.host):
                raise ValueError(_msg.format(net_mapping.ip, 'host', net_mapping.host))
            if not obj.find_network_by_name(net_mapping.network):
                raise ValueError(_msg.format(net_mapping.ip, 'network', net_mapping.network))
        return True

    @staticmethod
    def validate_router_mappings(obj, router_mappings):
        TopologyValidation.validate_name_mappings(obj, router_mappings)
        TopologyValidation.validate_cidrs_and_ips(obj, router_mappings)

        return True

    @staticmethod
    def validate_name_mappings(obj, router_mappings):
        _msg = 'Invalid router mapping with ip "{}". Cannot find {} with name "{}".'
        for router_mapping in router_mappings:
            if not obj.find_router_by_name(router_mapping.router):
                raise ValueError(_msg.format(router_mapping.ip, 'router', router_mapping.router))
            if not obj.find_network_by_name(router_mapping.network):
                raise ValueError(_msg.format(router_mapping.ip, 'network', router_mapping.network))

    @staticmethod
    def validate_cidrs_and_ips(obj, router_mappings):
        routers = [r for r in obj.routers]
        networks = [n for n in obj.networks]
        net_mappings = [n for n in obj.net_mappings]
        router_mappings_list = [r for r in router_mappings]

        TopologyValidation.raise_if_overlaps(routers + networks)

        TopologyValidation.raise_if_not_in_network(obj.net_mappings, obj.networks)
        TopologyValidation.raise_if_not_in_network(router_mappings, obj.networks)

        TopologyValidation.raise_if_ip_not_unique(net_mappings + router_mappings_list)

    @staticmethod
    def raise_if_overlaps(networks):
        cidrs = {network.name: ip_network(network.cidr) for network in networks}
        for net_a, net_b in combinations(cidrs, 2):
            if cidrs[net_a].overlaps(cidrs[net_b]):
                _msg = 'Network "{}" overlaps with network "{}".'
                raise ValueError(_msg.format(cidrs[net_a], cidrs[net_b]))

    @staticmethod
    def raise_if_not_in_network(mappings, networks):
        mappings = {mapp.network: ip_address(mapp.ip) for mapp in mappings}
        networks = {net.name: ip_network(net.cidr) for net in networks}
        for network, ip in mappings.items():
            if ip not in networks[network]:
                _msg = 'IP address "{}" is not valid host address of "{}" defined in network "{}".'
                raise ValueError(_msg.format(ip, networks[network], network))

    @staticmethod
    def raise_if_ip_not_unique(mappings):
        mappings_ip = [mapp.ip for mapp in mappings]
        duplicates_ip = TopologyValidation.get_duplicates(mappings_ip)

        if duplicates_ip:
            _msg = 'Uniqueness violation. The IP address of either of mappings must be unique. Incorrect IP addresses' \
                   ': {}.'
            raise ValueError(_msg.format(duplicates_ip))

    @staticmethod
    def validate_groups(obj, groups):
        TopologyValidation.raise_if_not_unique('groups', [g.name for g in groups])

        _msg = 'Invalid group with name "{}". Cannot find a node (host or router) with name "{}".'
        for group in groups:
            for node in group.nodes:
                if not obj.find_host_by_name(node) and not obj.find_router_by_name(node):
                    raise ValueError(_msg.format(group.name, node))
        return True

    @staticmethod
    def validate_group_nodes(obj, nodes):
        for node in nodes:
            if not re.match(VALID_NAMES_REGEX, node):
                _msg = 'Invalid name "{}" in Group.nodes. It does not match regex "{}".'
                raise ValueError(_msg.format(node, VALID_NAMES_REGEX))

        TopologyValidation.raise_if_not_unique('Group.nodes', list(nodes))

        return True

    @staticmethod
    def validate_name_uniqueness(obj, networks):
        a = [obj.name]
        b = [h.name for h in obj.hosts]
        c = [r.name for r in obj.routers]
        d = [n.name for n in networks]

        TopologyValidation.raise_if_not_unique('name, hosts, routers, networks', a + b + c + d)

        return True

    @staticmethod
    def raise_if_not_unique(what_for: str, elements: List):
        duplicates = TopologyValidation.get_duplicates(elements)
        if duplicates:
            raise ValueError(_UNIQ_MSG.format(what_for, duplicates))

    @staticmethod
    def get_duplicates(elements: List) -> List:
        result = set()

        unique_elements = set(elements)

        if len(elements) > len(unique_elements):
            for element in elements:
                if element not in unique_elements:
                    result.add(element)
                else:
                    unique_elements.remove(element)

        return list(result)
