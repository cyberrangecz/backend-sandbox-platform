""" This module handles network creation. """

import sys
from modules.routing import BORDER_ROUTER_NAME, BORDER_ROUTER_PUBLIC_IP


def _find_networks(hostname, mappings, device_type):
    """ Matches the device to networks. Returns a list of maiching networks. """
    network_list = []

    for mapping in mappings:
        if device_type in ('host', 'router'):
            if mapping[device_type] and mapping[device_type] == hostname:
                network_list.append(mapping)

    return network_list

def _add_ip(hostname, mappings, device_type, definitions):
    """ Adds a formatted ip address and network name to device definition. """

    networks = _find_networks(hostname, mappings, device_type)

    for network in networks:
        if not network["ip"]:
            print("Cannot find network mapping.")
            sys.exit()

        definitions[hostname].append(
            "device.vm.network :private_network, ip: \""
            + network["ip"] + '\", virtualbox__intnet: ' + network["network"])




def _add_netmask(hostname, my_network, networks, definitions):
    """ Adds netmask to the end of a formatted ip definition. """

    for network in networks:
        if network['name'] == my_network:
            address, mask = network['cidr'].split('/')
            definitions[hostname][-1] += (', netmask: \"' + mask + "\"")


def _add_interfaces(hostname, mapping, device_type, networks, definitions):
    """ Adds all network interfaces to a device. """

    if not mapping["ip"]:
        print("Cannot find network mapping.")
        sys.exit()

    definitions[hostname].append(
        "device.vm.network :private_network, ip: \"" + mapping["ip"]
        + "\", virtualbox__intnet: \"" + mapping["network"] + "\"")
    
    if hostname == BORDER_ROUTER_NAME:
        definitions[BORDER_ROUTER_NAME].append("device.vm.network :public_network, ip: \" " + BORDER_ROUTER_PUBLIC_IP + "\"") 

    _add_netmask(hostname, mapping["network"], networks, definitions)

def add_networks(hostname, yml, definitions):
    """ Adds ip address and natmask to a host. """

    if not yml['net_mappings']:
        return

    for mapping in yml['net_mappings']:
        if mapping['host'] == hostname:
            _add_interfaces(
                hostname, mapping,
                'host', yml['networks'], definitions)


def add_router_ip(routername, yml, definitions):
    """ Adds ip address to a router. """

    if not yml['router_mappings']:
        return

    for mapping in yml['router_mappings']:
        if mapping['router'] == routername:
            _add_interfaces(
                routername, mapping,
                'router', yml['networks'], definitions)
