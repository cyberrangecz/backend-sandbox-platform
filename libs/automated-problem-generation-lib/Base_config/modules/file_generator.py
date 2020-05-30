import jinja2
import os

from modules.device_creator import create_devices
from modules.ansible_data_generator import create_network_map, create_host_map, create_network_ips
from modules.routing import BORDER_ROUTER_IP, BORDER_ROUTER_NAME, BORDER_ROUTER_NETWORK_NAME, BORDER_ROUTER_PUBLIC_IP

def _load_template(template_name):
    """ Returns a loaded jinja2 template. """

    template_loader = jinja2.FileSystemLoader(searchpath="templates")
    template_env = jinja2.Environment(loader=template_loader, trim_blocks=True)
    return template_env.get_template(template_name)


def _generate_file(filename, output_string):
    """ Generates a file from output string. """

    try:
        new_file = open(filename, "w")
        new_file.write(output_string)
    except IOError:
        print("Error: cannot write to this location.")


def _create_role_directory(role_name, provisioning_dir):
    """ Creates directory structure for a role. """

    try:
        os.mkdir(provisioning_dir)
    except FileExistsError:
        pass
    try:
        os.mkdir(provisioning_dir + "/roles")
    except FileExistsError:
        pass
    try:
        os.mkdir(provisioning_dir + "/roles/" + role_name)
    except FileExistsError:
        pass
    try:
        os.mkdir(provisioning_dir + "/roles/" + role_name +"/tasks")
    except FileExistsError:
        pass


def _find_user_ansible_files(definitions):
    """ Finds the user ansible files and returns a list of host names. """
    host_names = []

    for host in definitions["hosts"]:
        if os.path.isfile("provisioning/" + host["name"] + ".yml" ):
            host_names.append(host["name"])

    return host_names

def generate_vagrantfile(definitions, ansible_local):
    """ Writes the prepared output to a Vagrantfile. """
   
    device_definitions = create_devices(definitions, ansible_local)
    user_ansible_files = _find_user_ansible_files(definitions)
    template = _load_template("vagrantfile")
    output = template.render(devices=device_definitions, user_files=user_ansible_files, ansible_local=ansible_local)
    _generate_file("Vagrantfile", output)
    
    print("Info: Vagrantfile successfully created.")


def _generate_playbook(definitions):
    """ Generates the main playbook. """

    host_map = create_host_map(definitions["net_mappings"], definitions["router_mappings"], definitions["hosts"])
    network = create_network_map(definitions)
    
    template = _load_template("playbook")
    output = template.render(hosts=host_map, routers=network)

    try:
        os.mkdir("provisioning")
    except FileExistsError:
        pass

    _generate_file("./provisioning/playbook.yml", output)


def _generate_device_configuration(definitions):
    """ Generates a playbook with basic device configutarion. """

    host_map = create_host_map(definitions["net_mappings"], definitions["router_mappings"], definitions["hosts"])
    network = create_network_map(definitions) 
    network_ips = create_network_ips(definitions["networks"])

    template = _load_template("device_configuration")
    output = template.render(hosts=host_map, routers=network, network_ips=network_ips, border_router_name = BORDER_ROUTER_NAME)

    try:
        os.mkdir("base_provisioning")
    except FileExistsError:
        pass

    _generate_file("./base_provisioning/device_configuration.yml", output)


def _generate_hosts_role(definitions):
    """ Generates hosts role. """

    host_map = create_host_map(definitions["net_mappings"], definitions["router_mappings"], definitions["hosts"])

    network = create_network_map(definitions)

    template = _load_template("hosts")
    output = template.render(hosts=host_map, routers=network)

    _create_role_directory("hosts", "base_provisioning")
    _generate_file("./base_provisioning/roles/hosts/tasks/main.yml", output)
    
    user_template = _load_template("user_hosts")
    user_output = template.render()

    _create_role_directory("hosts", "provisioning")
    _generate_file("./provisioning/roles/hosts/tasks/main.yml", output)


def _generate_separate_hosts_role(definitions):
    """ Generate roles for separate host devices. """
    
    host_map = create_host_map(definitions["net_mappings"], definitions["router_mappings"], definitions["hosts"])

    for host in definitions["hosts"]:

        for host_attributes in host_map:
            if host_attributes["host_name"] == host["name"]:
                host_name = host_attributes["host_name"]
                router_ip = host_attributes["router_ip"]
                interface = host_attributes["interface"]

        template = _load_template("separate_hosts")
        output = template.render(host_name=host_name, router_ip=router_ip, interface=interface)

        _create_role_directory(host["name"], "base_provisioning")
        _generate_file("./base_provisioning/roles/" + host["name"]  + "/tasks/main.yml", output)


        template = _load_template("user_separate_hosts")
        output = template.render(host_name=host_name)

        _create_role_directory(host["name"], "provisioning")
        _generate_file("./provisioning/roles/" + host["name"]  + "/tasks/main.yml", output)

def _generate_routers_role(definitions):
    """ Generates routers role. """

    if not definitions['routers'] or not definitions['router_mappings']:
        print("Info: No router definition was found. Skipping router creation.")
        return

    host_map = create_host_map(definitions["net_mappings"], definitions["router_mappings"], definitions["hosts"])

    network = create_network_map(definitions) 

    template = _load_template("routers")
    output = template.render(hosts=host_map, routers=network, border_router_ip=BORDER_ROUTER_IP)

    _create_role_directory("routers", "base_provisioning")
    _generate_file("./base_provisioning/roles/routers/tasks/main.yml", output)


def _find_cidr(network_name, definitions):
    """ Finds cidr of a network from name. """

    for network in definitions["networks"]:
        if network["name"] == network_name:
            return network["cidr"]

def _get_br_routers(definitions):
    """ Returns a list of router ips that are in the border router network. """

    br_mappings = dict()
    for router_mapping in definitions["router_mappings"]:
        if router_mapping["network"] == BORDER_ROUTER_NETWORK_NAME:
            for router_mapping2 in definitions["router_mappings"]:
                if router_mapping["router"] == router_mapping2["router"] and router_mapping["network"] != router_mapping2["network"]:
                    br_mappings[_find_cidr(router_mapping2["network"], definitions)] = router_mapping["ip"]

    return br_mappings

def _generate_br_role(definitions):
    """ Generates br role. """

    if not definitions['routers'] or not definitions['router_mappings']:
        print("Info: No router definition was found. Skipping border router creation.")
        return

    network = create_network_map(definitions) 
    
    host_map = create_host_map(definitions["net_mappings"], definitions["router_mappings"], definitions["hosts"])

    routers_in_br_network = _get_br_routers(definitions)

    template = _load_template("br")
    output = template.render(hosts = host_map, routers=network, br_routes=routers_in_br_network, border_router_name=BORDER_ROUTER_NAME, border_router_public_ip=BORDER_ROUTER_PUBLIC_IP)

    _create_role_directory("br", "base_provisioning")
    _generate_file("./base_provisioning/roles/br/tasks/main.yml", output)


def generate_ansible_files(device_definitions):
    """ Generates files for ansible. """
    
    _generate_playbook(device_definitions)
    _generate_device_configuration(device_definitions)
    _generate_hosts_role(device_definitions)
    _generate_separate_hosts_role(device_definitions)
    _generate_routers_role(device_definitions)
    _generate_br_role(device_definitions)

    print("Info: Ansible files successfully created.")
