import re

VALID_NAMES_REGEX = r'^[a-z]([a-z0-9A-Z-])*$'


# TODO add IP and CIDR validations
class TopologyValidation:
    @staticmethod
    def is_valid_ostack_name(self, name: str):
        if not re.match(VALID_NAMES_REGEX, name):
            _msg = 'Cannot set {}.name to "{}". It does not match regex "{}".'
            raise ValueError(_msg.format(self.__class__.__name__, name, VALID_NAMES_REGEX))

    @staticmethod
    def validate_net_mappings(self, net_mappings):
        _msg = 'Invalid network mapping with ip "{}". Cannot find {} with name "{}".'
        for net_mapping in net_mappings:
            if not self.find_host_by_name(net_mapping.host):
                raise ValueError(_msg.format(net_mapping.ip, 'host', net_mapping.host))
            if not self.find_network_by_name(net_mapping.network):
                raise ValueError(_msg.format(net_mapping.ip, 'network', net_mapping.network))
        return True

    @staticmethod
    def validate_router_mappings(self, router_mappings):
        _msg = 'Invalid router mapping with ip "{}". Cannot find {} with name "{}".'
        for router_mapping in router_mappings:
            if not self.find_router_by_name(router_mapping.router):
                raise ValueError(_msg.format(router_mapping.ip, 'router', router_mapping.router))
            if not self.find_network_by_name(router_mapping.network):
                raise ValueError(_msg.format(router_mapping.ip, 'network', router_mapping.network))
        return True

    @staticmethod
    def validate_groups(self, groups):
        _msg = 'Invalid group with name "{}". Cannot find a node (host or router) with name "{}".'
        for group in groups:
            for node in group.nodes:
                if not self.find_host_by_name(node) and not self.find_router_by_name(node):
                    raise ValueError(_msg.format(group.name, node))
        return True
