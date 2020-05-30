from modules.device_creator import open_file

INTERFACE_FILE = "name_mapping/interface.yml"

def create_network_map(definitions):
    """ Creates a structure with network topology for Jinja2 template. """

    routings = []

    for router_mapping in definitions["router_mappings"]:
        routing = dict()
        routing["router_name"] = router_mapping["router"]
        routing["router_network"] = router_mapping["network"]
        routing["router_network_ip"] = _find_network_ip(router_mapping["network"], definitions)
        routing["router_ip"] = router_mapping["ip"]
        routings.append(routing)

    return routings


def _find_router_ip(network_name, router_mappings):
    for router_mapping in router_mappings:
        if router_mapping["network"] == network_name:
            return router_mapping["ip"]

def _find_network_ip(network_name, definitions):
    for network in definitions["networks"]:
        if network["name"] == network_name:
            return network["cidr"]

def _find_interface(host_name, hosts):

    for host in hosts:
        if host["name"] == host_name:
            interfaces = open_file(INTERFACE_FILE)
            if host["base_box"] in interfaces:
                return interfaces[host["base_box"]]

    return "eth1"


def create_host_map(net_mappings, router_mappings, host_list):
    """ Creates a structure with hosts and their primary routers ip """

    hosts = []

    for net_mapping in net_mappings:
        host = dict()
        host["host_name"] = net_mapping["host"]
        host["host_ip"] = net_mapping["ip"]
        host["router_ip"] = _find_router_ip(
            net_mapping["network"], router_mappings)
        host["interface"] = _find_interface(net_mapping["host"], host_list)
        hosts.append(host)
    return hosts

def create_network_ips(networks):

    network_ips = []
    for network in networks:
        if network["cidr"] not in network_ips:
            network_ips.append(network["cidr"])

    return network_ips
