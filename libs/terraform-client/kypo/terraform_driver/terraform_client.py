from abc import ABC, abstractmethod
from enum import Enum

# Available cloud clients
from kypo.openstack_driver import KypoOpenStackClient

from kypo.topology_definition.models import TopologyDefinition

from kypo.terraform_driver.topology_instance import TopologyInstance


class AvailableCloudLibraries(Enum):
    OPENSTACK = KypoOpenStackClient


class KypoTerraformClient(ABC):
    """
    Client used as an interface providing functions of this Terraform library
    """

    def __init__(self, cloud_client: AvailableCloudLibraries, *args, **kwargs):
        self.cloud_client_class = cloud_client.value(*args, **kwargs)

    # create_stack
    def create_stack(self, topology_name: str, topology_definition: TopologyDefinition,
                     key_pair_name_ssh: str, key_pair_name_cert: str = None,
                     dry_run: bool = False):
        pass

    # wait for stack create action
    def wait_for_stack_creation(self):
        pass

    @abstractmethod
    def create_terraform_template(self, topology_definition: TopologyDefinition, *args, **kwargs):
        pass

    # wait for stack rollback action
    # TODO: is it needed?

    # delete stack
    def delete_stack(self):
        pass

    # wait for stack deletion action
    def wait_for_stack_deletion(self):
        pass

    # list stacks
    def list_allocated_topologies(self):
        pass

    def validate_topology_definition(self):
        pass

    def get_topology_instance(self):
        pass

    def get_enriched_topology_instance(self):
        pass

    def suspend_node(self):
        pass

    def resume_node(self):
        pass

    def reboot_node(self):
        pass

    def get_node(self):
        pass

    def get_console_url(self):
        pass

    # list stack events
    def list_stack_events(self):
        pass

    @abstractmethod
    def create_keypair(self, name: str, public_key: str = None, key_type: str = 'ssh'):
        pass

    @abstractmethod
    def get_keypair(self, name: str):
        pass

    @abstractmethod
    def delete_keypair(self, name: str):
        pass

    @abstractmethod
    def get_quota_set(self, name: str):
        pass

    @abstractmethod
    def get_project_name(self):
        pass

    def get_stack_status(self, stack_name: str):
        pass

    # both hardware usage methods should be implemented in this project
    @abstractmethod
    def validate_hardware_usage_of_stacks(self, topology_instance: TopologyInstance,
                                          count: int):
        pass

    @abstractmethod
    def get_hardware_usage(self, topology_instance: TopologyInstance):
        pass
