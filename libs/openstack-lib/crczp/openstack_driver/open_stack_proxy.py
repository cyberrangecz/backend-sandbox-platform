"""
OpenStackInstance manipulation functions.
"""

import os
import re
from typing import List, Dict

import novaclient.v2.keypairs
import structlog
from jinja2 import Environment, FileSystemLoader
from crczp.cloud_commons import (
    InvalidTopologyDefinition,
    TransformationConfiguration,
    CrczpException,
    StackException,
    Image,
    HardwareUsage,
    Limits,
    QuotaSet,
    Quota,
    SecurityGroups,
    TopologyInstance,
)
from crczp.topology_definition.models import Protocol
from novaclient.base import TupleWithMeta
from novaclient.exceptions import ClientException as NovaClientException, UnsupportedConsoleType as NovaUnsupportedConsoleType

from crczp.openstack_driver import utils

LOG = structlog.get_logger()
SEC_RULES_IP_PREFIX = "0.0.0.0/0"  # "147.251.0.0/16"

TEMPLATES_DIR_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "templates")
TERRAFORM_DEPLOY_TEMPLATE_FILE = "terraform-deploy-template.j2"
TERRAFORM_PROVIDER_TEMPLATE_FILE = "terraform-provider-template.j2"


def regex_replace(string, pattern="", replace=""):
    return re.sub(pattern, replace, string)


class OpenStackProxy:
    """
    Used for work with instances of virtual machines in openstack.

    Uses OpenStack glace, nova, neutron and heat client.
    """

    def __init__(
        self,
        glance_client: utils.glance_client.Client,
        nova_client: utils.nova_client.Client,
        neutron_client: utils.neutron_client.Client,
        auth_url: str,
        app_cred_id: str,
        app_cred_secret: str,
        trc: TransformationConfiguration,
    ):
        self.nova = nova_client
        self.glance = glance_client
        self.neutron = neutron_client
        self.auth_url = auth_url
        self.app_cred_id = app_cred_id
        self.app_cred_secret = app_cred_secret
        self.template_environment = Environment(loader=(FileSystemLoader(TEMPLATES_DIR_PATH)))
        self.template_environment.filters["regex_replace"] = regex_replace
        self.trc = trc

    @staticmethod
    def _get_owner_specified_data(image) -> Dict[str, str]:
        """
        Creates a dictionary of owner specified data from the image

        :param image: Image form glance.images
        :return: Dictionary with arbitrary number of owner_specified values from the image
        """
        return {k: v for (k, v) in image.items() if k.startswith("owner_specified.")}

    def list_images(self) -> List[Image]:
        """
        Gets image list

        :return: List of Image objects.
        """
        images = [
            Image(
                os_distro=image.get("os_distro"),
                os_type=image.get("os_type"),
                disk_format=image.get("disk_format"),
                container_format=image.get("container_format"),
                visibility=image.get("visibility"),
                size=image.get("size"),
                status=image.get("status"),
                min_ram=image.get("min_ram"),
                min_disk=image.get("min_disk"),
                created_at=image.get("created_at"),
                updated_at=image.get("updated_at"),
                tags=image.get("tags"),
                default_user=image.get("default_user"),
                name=image.get("name"),
                owner_specified=self._get_owner_specified_data(image),
            )
            for image in self.glance.images.list()
        ]

        return images

    def get_keypair(self, keypair_name: str) -> novaclient.v2.keypairs.Keypair:
        """
        Gets keypair from nova
        :param keypair_name: name of keypair to get
        :return: Keypair instance
        """
        try:
            return self.nova.keypairs.get(keypair_name)
        except NovaClientException as e:
            raise CrczpException("Failed to get keypair '{0}': {1}".format(keypair_name, e)) from e

    def create_keypair(self, name: str, public_key: str, key_type: str) -> novaclient.v2.keypairs.Keypair:
        """
        Create key-pair in nova.

        :param name: Name of ssh key in stack.
        :param public_key: key to be stored, if None new is created
        :param key_type: type of public key from which keypair is created,
                         accepted values are 'ssh' and 'x509'
        :return: Keypair instance
        """
        try:
            return self.nova.keypairs.create(name, public_key, key_type=key_type)
        except NovaClientException as e:
            raise CrczpException(f"Failed to create keypair '{name}' with public key '{public_key}': {e}") from e

    def delete_keypair(self, name: str) -> TupleWithMeta:
        """
        Delete keypair in OpenStack.

        :param name: name of key to be deleted.

        :return Nova client TupleWithMeta
        """
        try:
            return self.nova.keypairs.delete(name)
        except NovaClientException as e:
            raise CrczpException("Failed to delete keypair '{0}': {1}".format(name, e)) from e

    def resume_instance(self, node_id: str) -> None:
        """
        Resume instance.

        :param node_id: The ID of the node
        :return: None
        :raise CrczpException: Node not found
        """
        try:
            self.nova.servers.resume(node_id)
        except NovaClientException as e:
            raise CrczpException(f"Failed to resume instance with id={node_id}: {e}.") from e

    def start_instance(self, node_id: str) -> None:
        """
        Start instance.

        :param node_id: The ID of the node
        :return: None
        :raise CrczpException: Node not found
        """
        try:
            self.nova.servers.start(node_id)
        except NovaClientException as e:
            raise CrczpException(f"Failed to start instance with id={node_id}: {e}.") from e

    def reboot_instance(self, node_id: str) -> None:
        """
        Reboot instance.

        :param node_id: The ID of the node
        :return: None
        :raise CrczpException: Node not found
        """
        try:
            self.nova.servers.reboot(node_id)
        except NovaClientException as e:
            raise CrczpException(f"Failed to reboot instance with id={node_id}: {e}.") from e

    def get_image(self, image_id: str) -> Image:
        """
        Gets an Image object containing its details based on its id.

        :param image_id: id of the image to retrieve
        :return: Image
        """
        image_data = self.glance.images.get(image_id)
        return Image(
            os_distro=image_data.get("os_distro"),
            os_type=image_data.get("os_type"),
            disk_format=image_data.get("disk_format"),
            container_format=image_data.get("container_format"),
            visibility=image_data.get("visibility"),
            size=image_data.get("size"),
            status=image_data.get("status"),
            min_ram=image_data.get("min_ram"),
            min_disk=image_data.get("min_disk"),
            created_at=image_data.get("created_at"),
            updated_at=image_data.get("updated_at"),
            tags=image_data.get("tags"),
            default_user=image_data.get("default_user"),
            name=image_data.get("name"),
            owner_specified=self._get_owner_specified_data(image_data),
        )

    def get_console_url(self, node_id: str, console_type: str) -> str:
        """
        Get console for given node.

        :param node_id: The ID of the node
        :param console_type: Type can be novnc, xvpvnc, spice-html5, rdp-html5, serial and webmks
        :return: Console url
        :raise CrczpException: Node not found
        """
        try:
            spice_console = self.nova.servers.get_console_url(node_id, console_type)
        except (NovaUnsupportedConsoleType, NovaClientException) as e:
            raise StackException(f"OpenStack error while getting console: {e.message}") from e

        return spice_console["remote_console"]["url"]

    def get_quota_set(self, tenant_id: str) -> QuotaSet:
        """
        Get quota set of OpenStack project.

        :param tenant_id: ID of OpenStack project.
        :return QuotaSet object
        """
        nova_quotas = self.nova.quotas.get(tenant_id, detail=True)
        vcpu_quotas = nova_quotas.cores
        ram_quotas = nova_quotas.ram
        instances_quota = nova_quotas.instances

        # change MB to GB
        ram_quotas["limit"] = round(ram_quotas["limit"] / 1000.0, 1)
        ram_quotas["in_use"] = round(ram_quotas["in_use"] / 1000.0, 1)

        vcpu = Quota(vcpu_quotas["limit"], vcpu_quotas["in_use"])
        ram = Quota(ram_quotas["limit"], ram_quotas["in_use"])
        instances = Quota(instances_quota["limit"], instances_quota["in_use"])

        neutron_quotas = self.neutron.show_quota_details(tenant_id)["quota"]
        network_quotas = neutron_quotas["network"]
        subnet_quotas = neutron_quotas["subnet"]
        port_quotas = neutron_quotas["port"]

        network = Quota(network_quotas["limit"], network_quotas["used"])
        subnet = Quota(subnet_quotas["limit"], subnet_quotas["used"])
        port = Quota(port_quotas["limit"], port_quotas["used"])

        return QuotaSet(vcpu, ram, instances, network, subnet, port)

    @staticmethod
    def _get_flavors_dict(flavors) -> dict:
        """
        Gets flavors with their vcpu and ram usage

        :param flavors: Flavors defined in OpenStack project.
        :return: flavors dictionary
        """
        return {flavor.name: {"vcpu": flavor.vcpus, "ram": round(flavor.ram / 1000.0, 1)} for flavor in flavors}

    def get_flavors_dict(self) -> dict:
        """
        Gets flavors defined in OpenStack project with their vcpu and ram usage as dictionary

        :return: flavors dictionary
        """
        return self._get_flavors_dict(self.nova.flavors.list())

    def get_hardware_usage(self, topology_instance: TopologyInstance) -> HardwareUsage:
        """
        Gets hardware usage of topology instance.

        :param topology_instance: Topology instance of HeatStack
        :return: Hardware usage of Topology instance.
        """
        flavors = self.get_flavors_dict()
        used_vcpu, used_ram, used_instances = 0, 0, 0

        for node in topology_instance.get_nodes():
            flavor = flavors[node.flavor]
            used_vcpu += flavor["vcpu"]
            used_ram += flavor["ram"]
            used_instances += 1

        networks = [network for network in topology_instance.get_networks()]
        used_network = len(networks)
        used_subnet = len(networks)
        used_port = len([link for link in topology_instance.get_links()])

        return HardwareUsage(used_vcpu, used_ram, used_instances, used_network, used_subnet, used_port)

    def get_project_limits(self, tenant_id) -> Limits:
        """
        Get Absolute limits of OpenStack project.

        :param tenant_id: ID of OpenStack project.
        :return Limits of project
        """
        limits = {}
        for limit in self.nova.limits.get().absolute:
            if limit.name == "maxTotalCores":
                limits["vcpu"] = limit.value
            elif limit.name == "maxTotalRAMSize":
                # change MB to GB
                limits["ram"] = round(limit.value / 1000.0, 1)
            elif limit.name == "maxTotalInstances":
                limits["instances"] = limit.value

        neutron_limits = self.neutron.show_quota(tenant_id)["quota"]
        limits["network"] = neutron_limits["network"]
        limits["subnet"] = neutron_limits["subnet"]
        limits["port"] = neutron_limits["port"]

        return Limits(**limits)

    def get_terraform_provider(self) -> str:
        """
        Get OpenStack Terraform provider
        :return: Terraform provider template
        :raise InvalidTopologyDefinition: Terraform provider template is incorrect
        """
        try:
            template = self.template_environment.get_template(TERRAFORM_PROVIDER_TEMPLATE_FILE)
            return template.render(auth_url=self.auth_url, app_cred_id=self.app_cred_id, app_cred_secret=self.app_cred_secret)
        except Exception as e:
            raise InvalidTopologyDefinition("Error while generating provider template: ", e) from e

    def validate_and_get_terraform_template(
        self,
        topology_instance: TopologyInstance,
        key_pair_name_ssh: str = "dummy-ssh-key-pair",
        key_pair_name_cert: str = "ummy-cert-key-pair",
        resource_prefix: str = "stack-name",
    ) -> str:
        """
        Transform TopologyInstance into Terraform Template.

        :param topology_instance: TopologyInstance used to create template
        :param key_pair_name_ssh: The name of a SSH key pair saved in the cloud
        :param key_pair_name_cert: The name of certificate key pair in the cloud
        :param resource_prefix: The prefix of all resources.
        :return: Terraform Template as a string
        :raise: CrczpException on network validation error
        :raise: InvalidTopologyDefinition on template rendering error
        """
        try:
            template = self.template_environment.get_template(TERRAFORM_DEPLOY_TEMPLATE_FILE)
            template_str = template.render(
                topology_instance=topology_instance,
                trc=self.trc,
                key_pair_name_ssh=key_pair_name_ssh,
                key_pair_name_cert=key_pair_name_cert,
                protocol=Protocol,
                resource_prefix=resource_prefix,
                security_groups=SecurityGroups,
                auth_url=self.auth_url,
                app_cred_id=self.app_cred_id,
                app_cred_secret=self.app_cred_secret,
            )
        except Exception as e:
            raise InvalidTopologyDefinition("Error while generating template: ", e) from e

        return template_str
