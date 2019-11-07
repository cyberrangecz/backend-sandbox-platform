import re
from typing import List

VALID_NAMES_REGEX = r'^[a-z]([a-z0-9A-Z-])*$'


# TODO add IP and CIDR validations
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
        _msg = 'Invalid router mapping with ip "{}". Cannot find {} with name "{}".'
        for router_mapping in router_mappings:
            if not obj.find_router_by_name(router_mapping.router):
                raise ValueError(_msg.format(router_mapping.ip, 'router', router_mapping.router))
            if not obj.find_network_by_name(router_mapping.network):
                raise ValueError(_msg.format(router_mapping.ip, 'network', router_mapping.network))
        return True

    @staticmethod
    def validate_groups(obj, groups):
        _msg = 'Invalid group with name "{}". Cannot find a node (host or router) with name "{}".'
        for group in groups:
            for node in group.nodes:
                if not obj.find_host_by_name(node) and not obj.find_router_by_name(node):
                    raise ValueError(_msg.format(group.name, node))
        return True

    @staticmethod
    def validate_name_uniqueness(obj, networks):
        a = [obj.name]
        b = [h.name for h in obj.hosts]
        c = [r.name for r in obj.routers]
        d = [n.name for n in networks]

        duplicates = TopologyValidation.get_duplicates(a + b + c + d)
        if duplicates:
            _msg = 'Naming clash. The following identifiers are not unique within the definition: {}.'
            raise ValueError(_msg.format(duplicates))

        return True

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
