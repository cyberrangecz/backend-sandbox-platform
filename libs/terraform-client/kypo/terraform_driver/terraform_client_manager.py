import json
import os
import shutil
import subprocess
from typing import List

from kypo.cloud_commons import StackNotFound, KypoException, Image, TopologyInstance

from kypo.terraform_driver.terraform_client_elements import TerraformInstance

STACKS_DIR = '/var/tmp/kypo/terraform-stacks/'
TEMPLATE_FILE_NAME = 'deploy.tf'
TERRAFORM_STATE_FILE_NAME = 'terraform.tfstate'


class KypoTerraformClientManager:
    """
    Manager class for KypoTerraformClient
    """

    def __init__(self, stacks_dir, cloud_client, trc, template_file_name):
        self.cloud_client = cloud_client
        self.stacks_dir = stacks_dir if stacks_dir else STACKS_DIR
        self.template_file_name = template_file_name if template_file_name else TEMPLATE_FILE_NAME
        self.trc = trc
        self.create_directories(self.stacks_dir)

    @staticmethod
    def create_directories(dir_path: str) -> None:
        """
        Create directory and all subdirectories defined in path.

        :param dir_path: Directory path
        :return: None
        """
        os.makedirs(dir_path, exist_ok=True)

    @staticmethod
    def create_file(file_path: str, content: str) -> None:
        """
        Create file and write content to it.

        :param file_path: Path to the file
        :param content: The content of the file
        :return: None
        """
        with open(file_path, 'w') as file:
            file.write(content)
            file.flush()

    @staticmethod
    def remove_directory(dir_path: str) -> None:
        """
        Remove directory.

        :param dir_path: Directory path
        :return: None
        :raise StackNotFound: Terraform stack directory not found
        """
        try:
            shutil.rmtree(dir_path)
        except FileNotFoundError as exc:
            raise StackNotFound(exc)

    @staticmethod
    def wait_for_process(process) -> None:
        """
        Wait for the process to finish. Close all file descriptors when proces is finished.

        :param process: The process that is waited for
        :return: None
        :raise KypoException: Process finished with error
        """
        return_code = process.wait()
        if return_code:
            raise KypoException(f'Command failed, return code: {return_code}')

        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
        if process.stdin:
            process.stdin.close()

    @staticmethod
    def get_process_output(process) -> str:
        """
        Get the standard output of process.

        :param process: The process creating output
        :return: Standard output of process line by line
        """
        for stdout_line in iter(process.stdout.readline, ''):
            yield stdout_line

    def get_stack_dir(self, stack_name: str) -> str:
        """
        Get Terraform stack directory.

        :param stack_name: The name of Terraform stack
        :return: Path to the stack directory
        """
        return os.path.join(self.stacks_dir, stack_name)

    def init_terraform(self, stack_dir: str) -> None:
        """
        Initialize Terraform properties in stack directory.

        :param stack_dir: Path to the stack directory
        :return: None
        :raise KypoException: Terraform initialization failed
        """
        process = subprocess.Popen(['terraform', 'init'], cwd=stack_dir, stdout=subprocess.PIPE)
        self.wait_for_process(process)

    def create_terraform_template(self, topology_instance: TopologyInstance, *args, **kwargs)\
            -> str:
        """
        Create Terraform template.

        :param topology_instance: The TopologyDefinition from which the template is created
        :param args, kwargs: Can contain other attributes required for rendering of template
        :return: Rendered Terraform template
        :raise KypoException: Invalid template of attributes.
        """
        return self.cloud_client.create_terraform_template(topology_instance, *args, **kwargs)

    def create_stack(self, topology_instance: TopologyInstance, dry_run, stack_name: str,
                     key_pair_name_ssh: str, key_pair_name_cert: str, *args, **kwargs):
        """
        Create Terraform stack on the cloud.

        :param topology_instance: TopologyInstance from which is the stack created
        :param dry_run: Create only Terraform plan without allocation
        :param stack_name: The name of the stack
        :param key_pair_name_ssh: Name of the SSH key pair
        :param key_pair_name_cert: Name of the certificate key pair
        :param args, kwargs: Can contain other attributes required for rendering of template
        :return: The process that is executing the creation
        :raise KypoException: Stack creation has failed
        """
        terraform_template = self.create_terraform_template(topology_instance,
                                                            key_pair_name_ssh=key_pair_name_ssh,
                                                            key_pair_name_cert=key_pair_name_cert,
                                                            resource_prefix=stack_name, *args,
                                                            **kwargs)
        stack_dir = self.get_stack_dir(stack_name)
        self.create_directories(stack_dir)
        self.create_file(os.path.join(stack_dir, self.template_file_name), terraform_template)
        self.init_terraform(stack_dir)  # TODO: Can fail

        if dry_run:
            return subprocess.Popen(['terraform', 'plan'], cwd=stack_dir, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
        else:
            return subprocess.Popen(['terraform', 'apply', '-auto-approve', '-no-color'],
                                    cwd=stack_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True)

    def delete_stack(self, stack_name):
        """
        Delete Terraform stack.

        :param stack_name: Name of stack that is deleted
        :return: The process that is executing the deletion
        :raise KypoException: Stack deletion has failed
        """
        stack_dir = self.get_stack_dir(stack_name)
        return subprocess.Popen(['terraform', 'destroy', '-auto-approve', '-no-color'],
                                cwd=stack_dir, stdout=subprocess.PIPE, text=True)

    def delete_stack_directory(self, stack_name) -> None:
        """
        Delete the stack directory.

        :param stack_name: Name of stack
        :return: None
        :raise KypoException: Stack directory is not found
        """
        stack_dir = self.get_stack_dir(stack_name)
        self.remove_directory(stack_dir)

    def get_image(self, image_id) -> Image:
        """
        Get image data from cloud.

        :param image_id: ID of image
        :return: The image data as Image object
        """
        return self.cloud_client.get_image(image_id)

    def list_stacks(self) -> List[str]:
        """
        List created Terraform stacks.

        :return: The list containing stack names
        """
        return os.listdir(self.stacks_dir)

    def list_stack_resources(self, stack_name: str) -> List[dict]:
        """
        List stack resources and its attributes.

        :param stack_name: The name of stack
        :return: The list of dictionaries containing resources
        """
        stack_dir = self.get_stack_dir(stack_name)
        with open(os.path.join(stack_dir, TERRAFORM_STATE_FILE_NAME), 'r') as file:
            return list(filter(lambda res: res['mode'] == 'managed', json.load(file)['resources']))

    def get_resource_dict(self, stack_name) -> dict:
        """
        Get dictionary of resources. The keys are resource names and values are attributes

        :param stack_name: The name of stack
        :return: Dictionary of resources
        """
        list_of_resources = self.list_stack_resources(stack_name)
        return {res['name']: res['instances'] for res in list_of_resources}

    def get_resource_id(self, stack_name, node_name) -> str:
        """
        Get ID of stack's resource.

        :param stack_name: The name of stack
        :param node_name: The name of node
        :return: The ID of resource
        """
        resource_dict = self.get_resource_dict(stack_name)
        return resource_dict[f'{stack_name}-{node_name}'][0]['attributes']['id']

    def get_node(self, stack_name, node_name) -> TerraformInstance:
        """
        Get data about node.

        :param stack_name: The name of stack
        :param node_name: The name of node
        :return: TerraformInstance object
        """
        resource_dict = self.get_resource_dict(stack_name)
        resource_dict = resource_dict[f'{stack_name}-{node_name}'][0]['attributes']
        image_id = resource_dict['image_id']
        image = self.get_image(image_id)

        instance = TerraformInstance(name=node_name, instance_id=resource_dict['id'],
                                     status=resource_dict['power_state'], image=image,
                                     flavor_name=resource_dict['flavor_name'])

        for network in resource_dict['network']:
            name = network['name']
            link = {key: value for key, value in network.items() if key != 'name'}
            instance.add_link(name, link)

        return instance

    def get_console_url(self, stack_name, node_name, console_type: str) -> str:
        """
        Get console url of a node.

        :param stack_name: The name of stack
        :param node_name: The name of node
        :param console_type: Type can be novnc, xvpvnc, spice-html5, rdp-html5, serial and webmks
        :return: Url to console
        """
        node = self.get_node(stack_name, node_name)
        if node.status != 'active':
            raise KypoException(f'Cannot get {console_type} console from inactive machine')

        resource_id = self.get_resource_id(stack_name, node_name)
        return self.cloud_client.get_console_url(resource_id, console_type)

    def get_enriched_topology_instance(self, stack_name: str,
                                       topology_instance: TopologyInstance) -> TopologyInstance:
        """
        Get enriched TopologyInstance.

        Enriches TopologyInstance with openstack cloud instance data like
            port IP addresses and port mac addresses.

        :param stack_name: The name of stack
        :param topology_instance: The TopologyInstance
        :return: TopologyInstance with additional properties
        """
        topology_instance.name = stack_name
        list_of_resources = self.list_stack_resources(stack_name)
        resources_dict = {res['name']: res for res in list_of_resources}

        man_out_port_dict = resources_dict[f'{stack_name}-{self.trc.man_out_port}']
        topology_instance.ip = man_out_port_dict['instances'][0]['attributes']['all_fixed_ips'][0]

        for link in topology_instance.get_links():
            port_dict = resources_dict[f'{stack_name}-{link.name}']
            link.ip = port_dict['instances'][0]['attributes']['all_fixed_ips'][0]
            link.mac = port_dict['instances'][0]['attributes']['mac_address']

        return topology_instance
