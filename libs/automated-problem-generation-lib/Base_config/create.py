#!/usr/bin/python3
""" A script that generates a Vagrantfile from yaml. """

import sys
import jinja2

from modules.file_generator import generate_vagrantfile, generate_ansible_files
from modules.device_creator import open_file
from modules.routing import create_border_router

if len(sys.argv) == 3:
    if str(sys.argv[1]) == "-l":
        ansible_local = True
        input_file_name = str(sys.argv[2])
    elif str(sys.argv[2]) == "-l":
        ansible_local = True
        input_file_name = str(sys.argv[1])
    else:
        print("Error: Expecting a yml file and optionally a flag -l.")
        sys.exit()
elif len(sys.argv) == 2:
    ansible_local = False
    input_file_name = str(sys.argv[1])
else:
    print("Error: Expecting a yml file and optionally a flag -l.")
    sys.exit()

device_definitions = open_file(input_file_name)
create_border_router(device_definitions)

generate_vagrantfile(device_definitions, ansible_local)
generate_ansible_files(device_definitions)
