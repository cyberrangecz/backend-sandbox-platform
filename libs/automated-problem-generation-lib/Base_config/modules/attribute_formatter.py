""" This module handles the creation of simple vagrant commands from yaml
attributes. """

def _format_variable_name(key, variable_type, mappings):
    """ Formats the variable to the required vagrantfile definition. """
    return "device." + mappings[variable_type][key] + " = "


def _format_and_add(key, value, mappings, device_definition):
    """ Formats and adds the definition of a simple attribute. """

    if key in mappings["string"]:
        device_definition.append(
            _format_variable_name(key, "string", mappings)
            + '\"' + str(value) + '\"')
    elif key in mappings["integer"]:
        device_definition.append(
            _format_variable_name(key, "integer", mappings)
            + str(value))
    elif key in mappings["boolean"]:
        device_definition.append(
            _format_variable_name(key, "boolean", mappings)
            + str(value).lower())


def add_simple_commands(device, mappings, device_definition):
    """ Adds definitions of string, integer and boolean vagrant attributes. """

    for key, value in device.items():
        _format_and_add(key, value, mappings, device_definition)
