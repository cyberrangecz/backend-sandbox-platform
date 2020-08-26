""" This module handles VirtualBox attributes. """


def _print_flavor(host, flavors, provider_attributes, definitions):
    """ Formats and add a flavor for a device. """

    if 'memory' not in host:
        definitions[host['name']].append(
            '  vb.' + provider_attributes['memory'] + ' = '
            + str(flavors[host['flavor']]['memory']))
    if 'cpus' not in host:
        definitions[host['name']].append(
            '  vb.' + provider_attributes['cpus'] + ' = '
            + str(flavors[host['flavor']]['cores']))


def _add_params(host, flavors, provider_attributes, definitions):
    """ Formats and adds simple provision attributes. """

    if 'memory' in host:
        definitions[host['name']].append(
            '  vb.' + provider_attributes['memory'] + ' = '
            + str(host['memory']))
    if 'cpus' in host:
        definitions[host['name']].append(
            '  vb.' + provider_attributes['cpus'] + ' = '
            + str(host['cpus']))
    if 'flavor' in host and host['flavor'] in flavors:
        _print_flavor(host, flavors, provider_attributes, definitions)


def _need_provider(host, provider_attributes):
    """ Checks if provision attributes are present. """

    for attribute in provider_attributes:
        if attribute in host:
            return True
    return False


def add_prov_attributes(host, flavors, provider_attributes, definitions):
    """ Adds provider attributes. """

    if _need_provider(host, provider_attributes):
        definitions[host['name']].append(
            "device.vm.provider \"virtualbox\" do |vb|")
        _add_params(host, flavors, provider_attributes, definitions)
        definitions[host['name']].append("end")


def add_router_specification(router, definitions, ansible_local):
    """ Adds the default specification for a router. """

    router_box = "generic/debian10"
    router_memory = 256
    router_cpus = 1

    definitions[router['name']].append(
        "device.vm.hostname = \"" + router['name'] + "\"")
    definitions[router['name']].append(
        "device.vm.box = \"" + router_box + "\"")
    definitions[router['name']].append(
        "device.vm.provider \"virtualbox\" do |vb|")
    definitions[router['name']].append("  vb.memory = " + str(router_memory))
    definitions[router['name']].append(
        "  vb.cpus = " + str(router_cpus))
    definitions[router['name']].append("end")
