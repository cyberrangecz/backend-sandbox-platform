import os
import shutil
import subprocess
import json
from enum import Enum

from kypo.topology_definition.models import TopologyDefinition

from kypo.cloud_commons import KypoCloudClientBase, TopologyInstance, TransformationConfiguration,\
    Image, Limits, QuotaSet, HardwareUsage, KypoException, StackNotFound
# Available cloud clients
from kypo.openstack_driver import KypoOpenStackClient

from kypo.terraform_driver.terraform_client_elements import TerraformInstance

STACKS_DIR = '/var/tmp/kypo/terraform-stacks/'
TEMPLATE_FILE_NAME = 'deploy.tf'
TERRAFORM_STATE_FILE_NAME = 'terraform.tfstate'


class AvailableCloudLibraries(Enum):
    OPENSTACK = KypoOpenStackClient


class KypoTerraformClient:
    """
    Client used as an interface providing functions of this Terraform library
    """

    def __init__(self, cloud_client: AvailableCloudLibraries, trc: TransformationConfiguration,
                 stacks_dir: str = None, template_file_name: str = None, *args, **kwargs):
        self.cloud_client: KypoCloudClientBase = cloud_client.value(trc=trc, *args, **kwargs)
        self.stacks_dir = stacks_dir if stacks_dir else STACKS_DIR
        self.template_file_name = template_file_name if template_file_name else TEMPLATE_FILE_NAME
        self._create_directories(self.stacks_dir)
        self.trc = trc

    @staticmethod
    def _create_directories(dir_path: str) -> None:
        os.makedirs(dir_path, exist_ok=True)

    @staticmethod
    def _create_file(file_path: str, content: str) -> None:
        with open(file_path, 'w') as file:
            file.write(content)
            file.flush()

    @staticmethod
    def _remove_directory(dir_path: str) -> None:
        try:
            shutil.rmtree(dir_path)
        except FileNotFoundError as exc:
            raise StackNotFound(exc)

    @staticmethod
    def _execute_command(cmd, cwd):
        try:
            popen = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, text=True)
            for stdout_line in iter(popen.stdout.readline, ""):
                yield stdout_line
        except FileNotFoundError as exc:
            raise StackNotFound(exc)

        popen.stdout.close()
        return_code = popen.wait()
        if return_code:
            raise KypoException(return_code, cmd)

    @staticmethod
    def get_process_output(process):
        for stdout_line in iter(process.stdout.readline, ''):
            yield stdout_line

        # process.stdout.close()
        # return_code = process.wait()
        # if return_code:
        #     raise KypoException(return_code)

    def _init_terraform(self, stack_dir: str) -> None:
        list(self._execute_command(['terraform', 'init'], stack_dir))

    def _get_stack_dir(self, stack_name: str) -> str:
        return os.path.join(self.stacks_dir, stack_name)

    # create_stack
    def create_stack(self, stack_name: str, topology_definition: TopologyDefinition,
                     key_pair_name_ssh: str, key_pair_name_cert: str = None, dry_run: bool = False,
                     *args, **kwargs):
        terraform_template = self.create_terraform_template(topology_definition,
                                                            key_pair_name_ssh=key_pair_name_ssh,
                                                            key_pair_name_cert=key_pair_name_cert,
                                                            resource_prefix=stack_name, *args,
                                                            **kwargs)
        stack_dir = self._get_stack_dir(stack_name)
        self._create_directories(stack_dir)
        self._create_file(os.path.join(stack_dir, self.template_file_name), terraform_template)
        self._init_terraform(stack_dir)
        return subprocess.Popen(['terraform', 'apply', '-auto-approve', '-no-color'], cwd=stack_dir,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def create_terraform_template(self, topology_definition: TopologyDefinition, *args, **kwargs):
        return self.cloud_client.create_terraform_template(topology_definition, *args, **kwargs)

    def validate_topology_definition(self, topology_definition):
        self.create_terraform_template(topology_definition)

    def delete_stack(self, stack_name):
        stack_dir = self._get_stack_dir(stack_name)
        return subprocess.Popen(['terraform', 'destroy', '-auto-approve', '-no-color'],
                                cwd=stack_dir, stdout=subprocess.PIPE, text=True)
        # return self._execute_command(['terraform', 'destroy', '-auto-approve', '-no-color'], stack_dir)

    def delete_stack_directory(self, stack_name):
        stack_dir = self._get_stack_dir(stack_name)
        self._remove_directory(stack_dir)

    def list_images(self):
        return self.cloud_client.list_images()

    def list_stacks(self):
        return os.listdir(self.stacks_dir)

    def get_stack_status(self, stack_name: str):
        pass  # TODO: not used by sandbox-service

    def get_topology_instance(self, topology_definition: TopologyDefinition) -> TopologyInstance:
        return TopologyInstance(topology_definition, self.trc)

    def _get_resource_dict(self, stack_name):
        list_of_resources = self.list_stack_resources(stack_name)
        return {res['name']: res['instances'] for res in list_of_resources}

    def get_enriched_topology_instance(self, stack_name: str,
                                       topology_definition: TopologyDefinition) -> TopologyInstance:
        topology_instance = self.get_topology_instance(topology_definition)
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

    def get_image(self, image_id) -> Image:
        return self.cloud_client.get_image(image_id)

    def suspend_node(self, stack_name, node_name):
        # SUSPEND will brake terraform state.. DO NOT USE
        # resource_dict = self._get_resource_dict(stack_name)
        # resource_id = resource_dict[f'{stack_name}-{node_name}'][0]['attributes']['id']
        # self.cloud_client.suspend_node(resource_id)
        pass

    def resume_node(self, stack_name, node_name):
        resource_dict = self._get_resource_dict(stack_name)
        resource_id = resource_dict[f'{stack_name}-{node_name}'][0]['attributes']['id']
        self.cloud_client.resume_node(resource_id)

    def start_node(self, stack_name, node_name):
        resource_dict = self._get_resource_dict(stack_name)
        resource_id = resource_dict[f'{stack_name}-{node_name}'][0]['attributes']['id']
        self.cloud_client.start_node(resource_id)

    def reboot_node(self, stack_name, node_name):
        resource_dict = self._get_resource_dict(stack_name)
        resource_id = resource_dict[f'{stack_name}-{node_name}'][0]['attributes']['id']
        self.cloud_client.reboot_node(resource_id)

    def get_node(self, stack_name, node_name):
        resource_dict = self._get_resource_dict(stack_name)
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

    def get_console_url(self, stack_name, node_name, console_type: str):
        node = self.get_node(stack_name, node_name)
        if node.status != 'active':
            raise KypoException(f'Cannot get {console_type} console from inactive machine')

        resource_dict = self._get_resource_dict(stack_name)
        resource_id = resource_dict[f'{stack_name}-{node_name}'][0]['attributes']['id']
        return self.cloud_client.get_console_url(resource_id, console_type)

    # list stack events
    def list_stack_events(self, stack_name, *args, **kwargs):
        pass  # TODO: delete, wont be used, event will be stored in `output` just like ansible stage

    def list_stack_resources(self, stack_name: str):
        stack_dir = self._get_stack_dir(stack_name)
        with open(os.path.join(stack_dir, TERRAFORM_STATE_FILE_NAME), 'r') as file:
            return list(filter(lambda res: res['mode'] == 'managed', json.load(file)['resources']))

    def create_keypair(self, name: str, public_key: str = None, key_type: str = 'ssh'):
        return self.cloud_client.create_keypair(name, public_key, key_type)

    def get_keypair(self, name: str):
        return self.cloud_client.get_keypair(name)

    def delete_keypair(self, name: str):
        return self.cloud_client.delete_keypair(name)

    def get_quota_set(self) -> QuotaSet:
        return self.cloud_client.get_quota_set()

    def get_project_name(self):
        return self.cloud_client.get_project_name()

    def validate_hardware_usage_of_stacks(self, topology_instance: TopologyInstance, count: int):
        quota_set = self.get_quota_set()
        hardware_usage = self.get_hardware_usage(topology_instance) * count

        quota_set.check_limits(hardware_usage)

    def get_hardware_usage(self, topology_instance: TopologyInstance) -> HardwareUsage:
        return self.cloud_client.get_hardware_usage(topology_instance)

    def get_project_limits(self) -> Limits:
        return self.cloud_client.get_project_limits()
