from enum import Enum

from kypo.commons.cloud_client_abstract import KypoCloudClient

# Available cloud clients
from kypo.openstack_driver import KypoOpenStackClient

from kypo.topology_definition.models import TopologyDefinition

from kypo.terraform_driver.topology_instance import TopologyInstance


class AvailableCloudLibraries(Enum):
    OPENSTACK = KypoOpenStackClient


class KypoTerraformClient(KypoCloudClient):
    """
    Client used as an interface providing functions of this Terraform library
    """

    def list_images(self):
        pass

    def __init__(self, cloud_client: AvailableCloudLibraries, *args, **kwargs):
        self.cloud_client: KypoOpenStackClient = cloud_client.value(*args, **kwargs)

    # create_stack
    def create_stack(self, topology_name: str, topology_definition: TopologyDefinition,
                     key_pair_name_ssh: str, key_pair_name_cert: str = None,
                     dry_run: bool = False):
        print("creating stack")

    # wait for stack create action
    def wait_for_stack_creation(self, **kwargs):
        pass

    def create_terraform_template(self, topology_definition: TopologyDefinition, *args, **kwargs):
        return self.cloud_client.validate_topology_definition(topology_definition, *args,
                                                              **kwargs)

    # wait for stack rollback action
    # TODO: is it needed?

    # delete stack
    def delete_stack(self, stack_name, **kwargs):  # TODO: only *kwargs or combination?
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

    def create_keypair(self, name: str, public_key: str = None, key_type: str = 'ssh'):
        return self.cloud_client.create_keypair(name, public_key, key_type)

    def get_keypair(self, name: str):
        return self.cloud_client.get_keypair(name)

    def delete_keypair(self, name: str):
        return self.cloud_client.delete_keypair(name)

    def get_quota_set(self, name: str):
        return self.cloud_client.get_quota_set(name)  # TODO: create QuotaSet here

    def get_project_name(self):
        return self.cloud_client.get_project_name()

    def get_stack_status(self, stack_name: str):
        pass

    # TODO: both hardware usage methods should be implemented in this project
    def validate_hardware_usage_of_stacks(self, topology_instance: TopologyInstance,
                                          count: int):
        return self.cloud_client.validate_hardware_usage_of_stacks(topology_instance, count)

    def get_hardware_usage(self, topology_instance: TopologyInstance):
        return self.cloud_client.get_hardware_usage(topology_instance)
