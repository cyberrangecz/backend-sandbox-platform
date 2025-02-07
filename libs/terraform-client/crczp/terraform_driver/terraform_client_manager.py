import json
import os
import shutil
import subprocess
from typing import List, Tuple

from crczp.cloud_commons import CrczpCloudClientBase, StackNotFound, CrczpException, Image, TopologyInstance

from crczp.terraform_driver.terraform_client_elements import TerraformInstance
from crczp.terraform_driver.terraform_backend import CrczpTerraformBackend, TERRAFORM_STATE_FILE_NAME
from crczp.terraform_driver.terraform_exceptions import TerraformInitFailed, TerraformWorkspaceFailed
from crczp.terraform_driver.terraform_exc_handlers import command_error_handler

STACKS_DIR = '/var/tmp/crczp/terraform-stacks/'
TEMPLATE_FILE_NAME = 'deploy.tf'
TERRAFORM_BACKEND_FILE_NAME = 'backend.tf'
TERRAFORM_PROVIDER_FILE_NAME = 'provider.tf'
TERRAFORM_WORKSPACE_PATH = 'terraform.tfstate.d/{}/' + TERRAFORM_STATE_FILE_NAME
TERRAFORM_DEFAULT_WORKSPACE = 'default'
TERRAFORM_RETRY_NEW_WORKSPACE_COMMAND = 5


class CrczpTerraformClientManager:
    """
    Manager class for CrczpTerraformClient
    """

    def __init__(self, stacks_dir, cloud_client: CrczpCloudClientBase, trc, template_file_name,
                 terraform_backend: CrczpTerraformBackend):
        self.cloud_client = cloud_client
        self.stacks_dir = stacks_dir if stacks_dir else STACKS_DIR
        self.template_file_name = template_file_name if template_file_name else TEMPLATE_FILE_NAME
        self.trc = trc
        self.create_directories(self.stacks_dir)
        self.terraform_backend = terraform_backend

    @staticmethod
    def _execute_command(command: List[str], cwd: str, stdout=None, stderr=None)\
            -> subprocess.Popen:
        """
        Execute command in cwd and return subprocess.Popen object.

        :param command: Command to execute
        :param cwd: Working directory
        :param stdout: Redirect stdout to file
        :param stderr: Redirect stderr to file
        :return: subprocess.Popen object
        """
        return subprocess.Popen(command + ['-no-color'], cwd=cwd, stdout=stdout, stderr=stderr,
                                text=True)

    def _create_terraform_backend_file(self, stack_dir: str) -> None:
        """
        Create backend.tf file containing configuration for Terraform backend.

        :param stack_dir: The path to the stack directory
        :return: None
        """
        template = self.terraform_backend.template

        self.create_file(os.path.join(stack_dir, TERRAFORM_BACKEND_FILE_NAME), template)

    def _create_terraform_provider(self, stack_dir) -> None:
        """
        Create file with Terraform provider configuration.
        :param stack_dir: The path to the stack directory
        :return: None
        """
        provider = self.cloud_client.get_terraform_provider()

        self.create_file(os.path.join(stack_dir, TERRAFORM_PROVIDER_FILE_NAME), provider)

    def _initialize_stack_dir(self, stack_name: str, terraform_template: str = None) -> None:
        """

        :param stack_name: The name of Terraform stack.
        :param terraform_template: Terraform template specifying resources of the stack.
        :return: None
        :raise CrczpException: If should_raise is True and Terraform command fails.
        """
        stack_dir = self.get_stack_dir(stack_name)
        self.create_directories(stack_dir)
        self._create_terraform_backend_file(stack_dir)
        self._create_terraform_provider(stack_dir)

        if terraform_template:
            self.create_file(os.path.join(stack_dir, self.template_file_name), terraform_template)

        self.init_terraform(stack_dir, stack_name)

    def _pull_terraform_state(self, stack_name: str) -> None:
        """
        Pull Terraform state from remote backend.

        :param stack_name: The name of Terraform stack.
        :return: None
        """
        self._initialize_stack_dir(stack_name)
        stack_dir = self.get_stack_dir(stack_name)
        try:
            self._switch_terraform_workspace(stack_name, stack_dir)
        except TerraformWorkspaceFailed:
            raise CrczpException('Failed to switch Terraform workspace')

        terraform_state_file_path = os.path.join(stack_dir, TERRAFORM_STATE_FILE_NAME)
        terraform_state_file = open(terraform_state_file_path, 'w')
        command = ['tofu', 'state', 'pull']
        process = self._execute_command(command, cwd=stack_dir, stdout=terraform_state_file,
                                        stderr=subprocess.PIPE)
        _, stderr, return_code = self.wait_for_process(process)
        if return_code:
            command_error_handler(CrczpException, 'Failed to pull Terraform state',
                                  command=' '.join(command), stack_name=stack_name, stderr=stderr)

        terraform_state_file.flush()
        terraform_state_file.close()

    def _switch_terraform_workspace(self, workspace: str, stack_dir: str) -> None:
        """
        Switch Terraform workspace.

        :param workspace: The name of the workspace.
        :param stack_dir: The path to the stack directory
        :return: None
        """
        command = ['tofu', 'workspace', 'select', workspace]
        process = self._execute_command(command, cwd=stack_dir, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
        _, stderr, return_code = self.wait_for_process(process)
        if return_code:
            command_error_handler(TerraformWorkspaceFailed, 'Failed to switch Terraform workspace',
                                  command=' '.join(command), workspace=workspace, stderr=stderr)

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
    def wait_for_process(process, timeout=None) -> Tuple[str, str, int]:
        """
        Wait for process to finish and return stdout, stderr and return code.
        :param process: The process to wait for
        :param timeout: The timeout in seconds
        :return: Tuple of stdout, stderr and return code
        """
        stdout, stderr = process.communicate(timeout=timeout)
        stderr = ''.join(stderr.split('\n'))
        return_code = process.returncode
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
        if process.stdin:
            process.stdin.close()

        return stdout, stderr, return_code

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

    def init_terraform(self, stack_dir: str, stack_name: str) -> None:
        """
        Initialize Terraform properties in stack directory.

        :param stack_dir: Path to the stack directory
        :param stack_name: The name of Terraform stack
        :return: None
        :raise TerraformInitFailed: The 'terraform init' command fails.
        :raise TerraformWorkspaceFailed: Could not create new workspace.
        """
        command = ['tofu', 'init']
        process = self._execute_command(command, cwd=stack_dir,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, stderr, return_code = self.wait_for_process(process)
        if return_code:
            command_error_handler(TerraformInitFailed, 'Failed to initialize Terraform',
                                  command=' '.join(command), stack_name=stack_name, stderr=stderr)

    def create_terraform_workspace(self, stack_dir: str, stack_name: str,
                                   should_raise: bool = True) -> None:
        """
        Create new Terraform workspace.

        :param stack_dir: Path to the stack directory
        :param stack_name: The name of Terraform stack
        :param should_raise: Raise exception if workspace creation fails
        :return: None
        :raise TerraformWorkspaceFailed: Could not create new workspace.
        """
        retry_count = 1
        stderr = ""
        command = ['tofu', 'workspace', 'new', stack_name]
        while retry_count <= TERRAFORM_RETRY_NEW_WORKSPACE_COMMAND:
            process = self._execute_command(command, cwd=stack_dir, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE)
            _, stderr, return_code = self.wait_for_process(process)
            if not (return_code and should_raise) or ('already exists' in stderr):
                break
            retry_count += 1
        if retry_count > TERRAFORM_RETRY_NEW_WORKSPACE_COMMAND:
            command_error_handler(TerraformWorkspaceFailed, 'Failed to create new workspace: '
                                                            'command retry limit exceeded',
                                  command=' '.join(command), stack_name=stack_name, stderr=stderr)

    def create_terraform_template(self, topology_instance: TopologyInstance, *args, **kwargs)\
            -> str:
        """
        Create Terraform template.

        :param topology_instance: The TopologyDefinition from which the template is created
        :param args, kwargs: Can contain other attributes required for rendering of template
        :return: Rendered Terraform template
        :raise CrczpException: Invalid template of attributes.
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
        :raise CrczpException: Stack creation has failed
        """
        terraform_template = self.create_terraform_template(topology_instance,
                                                            key_pair_name_ssh=key_pair_name_ssh,
                                                            key_pair_name_cert=key_pair_name_cert,
                                                            resource_prefix=stack_name, *args,
                                                            **kwargs)
        stack_dir = self.get_stack_dir(stack_name)
        self._initialize_stack_dir(stack_name, terraform_template)
        self.create_terraform_workspace(stack_dir, stack_name)

        if dry_run:
            return self._execute_command(['tofu', 'plan'], cwd=stack_dir,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        return self._execute_command(['tofu', 'apply', '-auto-approve', '-no-color'],
                                     cwd=stack_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def delete_stack(self, stack_name):
        """
        Delete Terraform stack.

        :param stack_name: Name of stack that is deleted
        :return: The process that is executing the deletion
        :raise CrczpException: Stack deletion has failed
        """
        stack_dir = self.get_stack_dir(stack_name)
        try:
            self._initialize_stack_dir(stack_name)
            self._switch_terraform_workspace(stack_name, stack_dir)
        except (TerraformInitFailed, TerraformWorkspaceFailed):
            return None
        return self._execute_command(['tofu', 'destroy', '-auto-approve', '-no-color'],
                                     cwd=stack_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def delete_stack_directory(self, stack_name) -> None:
        """
        Delete the stack directory.

        :param stack_name: Name of stack
        :return: None
        :raise CrczpException: Stack directory is not found
        """
        stack_dir = self.get_stack_dir(stack_name)
        self.remove_directory(stack_dir)

    def delete_terraform_workspace(self, stack_name) -> None:
        """
        Delete Terraform workspace.

        :param stack_name: Name of stack
        :return: None
        :raise CrczpException: Terraform workspace is not found
        """
        stack_dir = self.get_stack_dir(stack_name)
        self._switch_terraform_workspace(TERRAFORM_DEFAULT_WORKSPACE, stack_dir)
        command = ['tofu', 'workspace', 'delete', stack_name]
        process = self._execute_command(command, cwd=stack_dir, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
        _, stderr, return_code = self.wait_for_process(process)
        if return_code:
            command_error_handler(TerraformWorkspaceFailed, 'Failed to delete Terraform workspace',
                                  command=' '.join(command), stderr=stderr)

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
        self._pull_terraform_state(stack_name)
        stack_dir = self.get_stack_dir(stack_name)
        with open(os.path.join(stack_dir, TERRAFORM_STATE_FILE_NAME), 'r')\
                as file:
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
        node_details = self.cloud_client.get_node_details(resource_dict)
        image_id = node_details.image_id
        if image_id == "Attempt to boot from volume - no image supplied":
            if 'block_device' in resource_dict and len(resource_dict['block_device']) \
                    and 'uuid' in resource_dict['block_device'][0]:
                image_id = resource_dict['block_device'][0]['uuid']
            else:
                raise CrczpException('Image id could not be retrieved from the node')

        image = self.get_image(image_id)
        status = node_details.status
        flavor = node_details.flavor
        instance = TerraformInstance(name=node_name, instance_id=resource_dict['id'],
                                     status=status, image=image,
                                     flavor_name=flavor)

        for network in resource_dict.get('network', []):
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
            raise CrczpException(f'Cannot get {console_type} console from inactive machine')

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

        man_out_port_dict = resources_dict[f'{stack_name}-{self.trc.man_out_port}']['instances'][0]['attributes']
        topology_instance.ip = self.cloud_client.get_private_ip(man_out_port_dict)

        for link in topology_instance.get_links():
            port_dict = resources_dict[f'{stack_name}-{link.name}']['instances'][0]['attributes']
            link.ip = self.cloud_client.get_private_ip(port_dict)
            link.mac = port_dict['mac_address']

        return topology_instance
