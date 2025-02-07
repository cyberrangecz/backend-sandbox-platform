from enum import Enum
from typing import List, Tuple

from crczp.cloud_commons import CrczpCloudClientBase, TopologyInstance, TransformationConfiguration, \
    Image, Limits, QuotaSet, HardwareUsage
# Available cloud clients
from crczp.openstack_driver import CrczpOpenStackClient
from crczp.aws_driver.aws_client import CrczpAwsClient
from crczp.topology_definition.models import TopologyDefinition, DockerContainers

from crczp.terraform_driver.terraform_backend import CrczpTerraformBackend
from crczp.terraform_driver.terraform_client_elements import TerraformInstance, \
    CrczpTerraformBackendType
from crczp.terraform_driver.terraform_client_manager import CrczpTerraformClientManager


class AvailableCloudLibraries(Enum):
    OPENSTACK = CrczpOpenStackClient
    AWS = CrczpAwsClient


class CrczpTerraformClient:
    """
    Client used as an interface providing functions of this Terraform library
    """

    def __init__(self, cloud_client: AvailableCloudLibraries, trc: TransformationConfiguration,
                 stacks_dir: str = None, template_file_name: str = None,
                 backend_type: CrczpTerraformBackendType = CrczpTerraformBackendType('local'),
                 db_configuration=None, kube_namespace=None, *args, **kwargs):
        self.cloud_client: CrczpCloudClientBase = cloud_client.value(trc=trc, *args, **kwargs)
        terraform_backend = CrczpTerraformBackend(backend_type=backend_type,
                                                 db_configuration=db_configuration,
                                                 kube_namespace=kube_namespace)
        self.client_manager = CrczpTerraformClientManager(stacks_dir, self.cloud_client, trc,
                                                         template_file_name, terraform_backend)
        self.trc = trc

    def get_process_output(self, process):
        """
        Get the standard output of process.

        :param process: The process creating output
        :return: Standard output of process line by line
        """
        return self.client_manager.get_process_output(process)

    def wait_for_process(self, process, timeout) -> Tuple[str, str, int]:
        """
        Wait for the process to finish. Close all file descriptors when proces is finished.

        :param process: The process that is waited for
        :param timeout: Timeout in seconds
        :return: Tuple of stdout, stderr and return code
        """
        return self.client_manager.wait_for_process(process, timeout)

    def create_stack(self, topology_definition: TopologyDefinition, stack_name: str = 'stack-name',
                     key_pair_name_ssh: str = 'dummy-ssh-key-pair',
                     key_pair_name_cert: str = 'dummy-cert-key-pair', dry_run: bool = False,
                     *args, **kwargs):
        """
        Create Terraform stack on the cloud.

        :param stack_name: The name of the stack
        :param topology_definition: TopologyDefinition from which is the stack created
        :param key_pair_name_ssh: Name of the SSH key pair
        :param key_pair_name_cert: Name of the certificate key pair
        :param dry_run: Create only Terraform plan without allocation
        :param args, kwargs: Can contain other attributes required for rendering of template
        :return: The process that is executing the creation
        :raise CrczpException: Stack creation has failed
        """
        topology_instance = self.get_topology_instance(topology_definition)
        return self.client_manager.create_stack(topology_instance, dry_run, stack_name,
                                                key_pair_name_ssh, key_pair_name_cert, *args,
                                                **kwargs)

    def create_terraform_template(self, topology_definition: TopologyDefinition, *args, **kwargs)\
            -> str:
        """
        Create Terraform template.

        :param topology_definition: The TopologyDefinition from which the template is created
        :param args, kwargs: Can contain other attributes required for rendering of template
        :return: Rendered Terraform template
        :raise CrczpException: Invalid template of attributes.
        """
        topology_instance = self.get_topology_instance(topology_definition)
        return self.client_manager.create_terraform_template(topology_instance, *args, **kwargs)

    def validate_topology_definition(self, topology_definition: TopologyDefinition) -> None:
        """
        Validate the topology definition.

        :param topology_definition: TopologyDefinition object
        :return: None
        :raise CrczpException: TopologyDefinition is invalid
        """
        self.create_terraform_template(topology_definition)

    def delete_stack(self, stack_name: str):
        """
        Delete Terraform stack.

        :param stack_name: Name of stack that is deleted
        :return: The process that is executing the deletion
        :raise CrczpException: Stack deletion has failed
        """
        return self.client_manager.delete_stack(stack_name)

    def delete_stack_directory(self, stack_name: str) -> None:
        """
        Delete the stack directory.

        :param stack_name: Name of stack
        :return: None
        :raise CrczpException: Stack directory is not found
        """
        self.client_manager.delete_stack_directory(stack_name)

    def delete_terraform_workspace(self, stack_name: str) -> None:
        """
        Delete the Terraform workspace.

        :param stack_name: Name of stack
        :return: None
        :raise CrczpException: Terraform workspace is not found
        """
        self.client_manager.delete_terraform_workspace(stack_name)

    def list_images(self) -> List[Image]:
        """
        List all available images on the cloud project.

        :return: List of Image objects.
        """
        return self.cloud_client.list_images()

    def list_stacks(self) -> List[str]:
        """
        List created Terraform stacks.

        :return: The list containing stack names
        """
        return self.client_manager.list_stacks()

    def get_topology_instance(self, topology_definition: TopologyDefinition,
                              containers: DockerContainers = None)\
            -> TopologyInstance:
        """
        Get TopologyInstance from topology definition.

        :param topology_definition: TopologyDefinition object
        :param containers: DockerContainers object
        :return: TopologyInstance object
        """
        return TopologyInstance(topology_definition, self.trc, containers)

    def get_enriched_topology_instance(self, stack_name: str, topology_definition: TopologyDefinition,
                                       containers: DockerContainers = None) -> TopologyInstance:
        """
        Get enriched TopologyInstance.

        Enriches TopologyInstance with openstack cloud instance data like
            port IP addresses and port mac addresses.

        :param stack_name: The name of stack
        :param topology_definition: TopologyDefinition object
        :param containers: DockerContainers object
        :return: TopologyInstance with additional properties
        """
        topology_instance = self.get_topology_instance(topology_definition, containers)
        return self.client_manager.get_enriched_topology_instance(stack_name, topology_instance)

    def get_image(self, image_id: str) -> Image:
        """
        Get Image object based on its ID.

        :param image_id: The ID of image on the cloud
        :return: Image object
        """
        return self.cloud_client.get_image(image_id)

    def resume_node(self, stack_name: str, node_name: str) -> None:
        """
        Resume node.

        :param stack_name: The name of stack
        :param node_name: The name of node
        :return: None
        :raise CrczpException: Node not found
        """
        resource_id = self.client_manager.get_resource_id(stack_name, node_name)
        self.cloud_client.resume_node(resource_id)

    def start_node(self, stack_name: str, node_name: str) -> None:
        """
        Start node.

        :param stack_name: The name of stack
        :param node_name: The name of node
        :return: None
        :raise CrczpException: Node not found
        """
        resource_id = self.client_manager.get_resource_id(stack_name, node_name)
        self.cloud_client.start_node(resource_id)

    def reboot_node(self, stack_name: str, node_name: str) -> None:
        """
        Reboot node.

        :param stack_name: The name of stack
        :param node_name: The name of node
        :return: None
        :raise CrczpException: Node not found
        """
        resource_id = self.client_manager.get_resource_id(stack_name, node_name)
        self.cloud_client.reboot_node(resource_id)

    def get_node(self, stack_name: str, node_name: str) -> TerraformInstance:
        """
        Get data about node.

        :param stack_name: The name of stack
        :param node_name: The name of node
        :return: TerraformInstance object
        """
        return self.client_manager.get_node(stack_name, node_name)

    def get_console_url(self, stack_name: str, node_name: str, console_type: str) -> str:
        """
        Get console url of a node.

        :param stack_name: The name of stack
        :param node_name: The name of node
        :param console_type: Type can be novnc, xvpvnc, spice-html5, rdp-html5, serial and webmks
        :return: Url to console
        """
        return self.client_manager.get_console_url(stack_name, node_name, console_type)

    def list_stack_resources(self, stack_name: str) -> List[dict]:
        """
        List stack resources and its attributes.

        :param stack_name: The name of stack
        :return: The list of dictionaries containing resources
        """
        return self.client_manager.list_stack_resources(stack_name)

    def create_keypair(self, name: str, public_key: str = None, key_type: str = 'ssh') -> None:
        """
        Create key pair in cloud.

        :param name: Name of the key pair
        :param public_key: SSH public key or certificate, it None new is created
        :param key_type: Accepted vales are 'ssh' and 'x509'. Is used as suffix to 'name' parameter
        :return: None
        :raise CrczpException: Creation failure
        """
        self.cloud_client.create_keypair(name, public_key, key_type)

    def get_keypair(self, name: str):
        """
        Get KeyPair instance from cloud.

        :param name: The name of key pair
        :return: KeyPair instance
        :raise CrczpException: Key pair does not exist
        """
        return self.cloud_client.get_keypair(name)

    def delete_keypair(self, name: str) -> None:
        """
        Delete key pair.

        :param name: The name of key pair
        :return: None
        :raise CrczpException: Key pair does not exist
        """
        self.cloud_client.delete_keypair(name)

    def get_quota_set(self) -> QuotaSet:
        """
        Get quota set of cloud project.

        :return: QuotaSet object
        """
        return self.cloud_client.get_quota_set()

    def get_project_name(self) -> str:
        """
        Get project name from application credentials.

        :return: The name of the cloud project
        """
        return self.cloud_client.get_project_name()

    def validate_hardware_usage_of_stacks(self, topology_instance: TopologyInstance, count: int)\
            -> None:
        """
        Validate hardware usage of Terraform stack.

        :param topology_instance: TopologyInstance of the stack
        :param count: Number of stacks
        :return: None
        :raise CrczpException: The cloud limits are exceeded
        """
        quota_set = self.get_quota_set()
        hardware_usage = self.get_hardware_usage(topology_instance) * count

        quota_set.check_limits(hardware_usage)

    def get_hardware_usage(self, topology_instance: TopologyInstance) -> HardwareUsage:
        """
        Get hardware usage of a single sandbox.

        :param topology_instance: Topology instance from which the sandbox is created
        :return: HardwareUsage object
        """
        return self.cloud_client.get_hardware_usage(topology_instance)

    def get_flavors_dict(self) -> dict:
        """
        Gets flavors defined in OpenStack project with their vcpu and ram usage as dictionary

        :return: flavors dictionary
        """
        return self.cloud_client.get_flavors_dict()

    def get_project_limits(self) -> Limits:
        """
        Get resources limits of cloud project.

        :return: Limits object
        """
        return self.cloud_client.get_project_limits()
