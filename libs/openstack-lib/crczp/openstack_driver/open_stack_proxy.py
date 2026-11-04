"""
OpenStackInstance manipulation functions.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any, TypedDict

import novaclient.v2.keypairs
import structlog
from jinja2 import Environment, FileSystemLoader, select_autoescape
from neutronclient.common.exceptions import NotFound as NeutronNotFound
from novaclient.base import TupleWithMeta
from novaclient.exceptions import ClientException as NovaClientException
from novaclient.exceptions import NotFound as NovaNotFound
from novaclient.exceptions import UnsupportedConsoleType as NovaUnsupportedConsoleType

from crczp.cloud_commons import (
    CloudResource,
    CrczpException,
    HardwareUsage,
    Image,
    InvalidTopologyDefinition,
    Limits,
    Quota,
    QuotaSet,
    ResourceDeletion,
    ResourceKind,
    SecurityGroups,
    StackException,
    StackInventory,
    StackNameScheme,
    TaggedResource,
    TopologyInstance,
    TransformationConfiguration,
    delete_in_order,
    group_by_stack,
    hcl_string,
    select_stack,
)
from crczp.openstack_driver.network_forwarding import (
    TapMirror,
    build_tap_mirrors,
    tunnel_id_base,
    validate_destination_network,
)
from crczp.topology_definition.models import Protocol

if TYPE_CHECKING:
    from glanceclient.v2 import client as glance_client
    from neutronclient.v2_0 import client as neutron_client

    # `novaclient.client.Client` is a version-dispatching factory function, not a class;
    # `utils.get_client('nova', ...)` builds a 2.x client, so annotate the concrete class.
    from novaclient.v2 import client as nova_client

LOG = structlog.get_logger()
SEC_RULES_IP_PREFIX = '0.0.0.0/0'  # "147.251.0.0/16"

TEMPLATES_DIR_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'templates')
TERRAFORM_DEPLOY_TEMPLATE_FILE = 'terraform-deploy-template.j2'
TERRAFORM_PROVIDER_TEMPLATE_FILE = 'terraform-provider-template.j2'
VALIDATION_RESOURCE_PREFIX = 'stack-name'
TAP_MIRROR = 'tap_mirror'
FLOATING_IP = 'floating_ip'
SERVER = 'server'
ROUTER = 'router'
PORT = 'port'
SUBNET = 'subnet'
NETWORK = 'network'
TAP_MIRRORS_PATH = '/taas/tap_mirrors'
SERVER_DELETION_TIMEOUT_SECONDS = 300
SERVER_DELETION_POLL_SECONDS = 2


def regex_replace(string: str, pattern: str = '', replace: str = '') -> str:
    """Apply a regex substitution on *string* (used as a Jinja2 filter)."""
    return re.sub(pattern, replace, string)


class FlavorUsage(TypedDict):
    """Hardware consumed by a single OpenStack flavor: whole vCPUs and RAM in GB."""

    vcpu: int
    ram: float


class OpenStackProxy:  # pylint: disable=too-many-instance-attributes
    """
    Used for work with instances of virtual machines in openstack.

    Uses OpenStack glace, nova, neutron and heat client.
    """

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        glance_client: glance_client.Client,
        nova_client: nova_client.Client,
        neutron_client: neutron_client.Client,
        auth_url: str,
        app_cred_id: str,
        app_cred_secret: str,
        trc: TransformationConfiguration,
    ) -> None:
        self.nova = nova_client
        self.glance = glance_client
        self.neutron = neutron_client
        self.auth_url = auth_url
        self.app_cred_id = app_cred_id
        self.app_cred_secret = app_cred_secret
        self.template_environment = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR_PATH),
            autoescape=select_autoescape(),
        )
        self.template_environment.filters['regex_replace'] = regex_replace
        self.template_environment.filters['hcl_string'] = hcl_string
        self.trc = trc

    @staticmethod
    def _get_owner_specified_data(image: Any) -> dict[str, str]:
        """
        Creates a dictionary of owner specified data from the image

        :param image: Image form glance.images
        :return: Dictionary with arbitrary number of owner_specified values from the image
        """
        return {k: v for (k, v) in image.items() if k.startswith('owner_specified.')}

    def list_images(self) -> list[Image]:
        """
        Gets image list

        :return: List of Image objects.
        """
        images = [
            Image(
                os_distro=image.get('os_distro'),
                os_type=image.get('os_type'),
                disk_format=image.get('disk_format'),
                container_format=image.get('container_format'),
                visibility=image.get('visibility'),
                size=image.get('size'),
                status=image.get('status'),
                min_ram=image.get('min_ram'),
                min_disk=image.get('min_disk'),
                created_at=image.get('created_at'),
                updated_at=image.get('updated_at'),
                tags=image.get('tags'),
                default_user=image.get('default_user'),
                name=image.get('name'),
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
            raise CrczpException(f"Failed to get keypair '{keypair_name}': {e}") from e

    def create_keypair(
        self, name: str, public_key: str | None, key_type: str
    ) -> novaclient.v2.keypairs.Keypair:
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
            msg = f"Failed to create keypair '{name}' with public key '{public_key}': {e}"
            raise CrczpException(msg) from e

    def delete_keypair(self, name: str) -> TupleWithMeta:
        """
        Delete keypair in OpenStack.

        :param name: name of key to be deleted.

        :return Nova client TupleWithMeta
        """
        try:
            return self.nova.keypairs.delete(name)
        except NovaClientException as e:
            raise CrczpException(f"Failed to delete keypair '{name}': {e}") from e

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
            raise CrczpException(f'Failed to resume instance with id={node_id}: {e}.') from e

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
            raise CrczpException(f'Failed to start instance with id={node_id}: {e}.') from e

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
            raise CrczpException(f'Failed to reboot instance with id={node_id}: {e}.') from e

    def get_image(self, image_id: str) -> Image:
        """
        Gets an Image object containing its details based on its id.

        :param image_id: id of the image to retrieve
        :return: Image
        """
        image_data = self.glance.images.get(image_id)
        return Image(
            os_distro=image_data.get('os_distro'),
            os_type=image_data.get('os_type'),
            disk_format=image_data.get('disk_format'),
            container_format=image_data.get('container_format'),
            visibility=image_data.get('visibility'),
            size=image_data.get('size'),
            status=image_data.get('status'),
            min_ram=image_data.get('min_ram'),
            min_disk=image_data.get('min_disk'),
            created_at=image_data.get('created_at'),
            updated_at=image_data.get('updated_at'),
            tags=image_data.get('tags'),
            default_user=image_data.get('default_user'),
            name=image_data.get('name'),
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
            raise StackException(f'OpenStack error while getting console: {e.message}') from e

        return str(spice_console['remote_console']['url'])

    def get_quota_set(self, tenant_id: str) -> QuotaSet:  # pylint: disable=too-many-locals
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
        ram_quotas['limit'] = round(ram_quotas['limit'] / 1000.0, 1)
        ram_quotas['in_use'] = round(ram_quotas['in_use'] / 1000.0, 1)

        vcpu = Quota(vcpu_quotas['limit'], vcpu_quotas['in_use'])
        ram = Quota(ram_quotas['limit'], ram_quotas['in_use'])
        instances = Quota(instances_quota['limit'], instances_quota['in_use'])

        neutron_quotas = self.neutron.show_quota_details(tenant_id)['quota']
        network_quotas = neutron_quotas['network']
        subnet_quotas = neutron_quotas['subnet']
        port_quotas = neutron_quotas['port']

        network = Quota(network_quotas['limit'], network_quotas['used'])
        subnet = Quota(subnet_quotas['limit'], subnet_quotas['used'])
        port = Quota(port_quotas['limit'], port_quotas['used'])

        return QuotaSet(vcpu, ram, instances, network, subnet, port)

    @staticmethod
    def _get_flavors_dict(flavors: Any) -> dict[str, FlavorUsage]:
        """
        Gets flavors with their vcpu and ram usage

        :param flavors: Flavors defined in OpenStack project.
        :return: flavors dictionary
        """
        return {
            flavor.name: FlavorUsage(vcpu=flavor.vcpus, ram=round(flavor.ram / 1000.0, 1))
            for flavor in flavors
        }

    def get_flavors_dict(self) -> dict[str, FlavorUsage]:
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
        used_vcpu = 0
        used_ram: float = 0
        used_instances = 0

        for node in topology_instance.get_nodes():
            if node.flavor not in flavors:
                raise ValueError(
                    f"Flavor '{node.flavor}' used in topology is not defined in "
                    f'OpenStack project. Please fix your flavor mappings and man '
                    f'flavor in your configuration.'
                )
            flavor = flavors[node.flavor]
            used_vcpu += flavor['vcpu']
            used_ram += flavor['ram']
            used_instances += 1

        networks = list(topology_instance.get_networks())
        used_network = len(networks)
        used_subnet = len(networks)
        used_port = len(list(topology_instance.get_links()))
        if topology_instance.get_network_forwarding():
            used_port += 1  # the forwarding router's interface on the destination network

        return HardwareUsage(
            used_vcpu, used_ram, used_instances, used_network, used_subnet, used_port
        )

    def get_project_limits(self, tenant_id: str) -> Limits:
        """
        Get Absolute limits of OpenStack project.

        :param tenant_id: ID of OpenStack project.
        :return Limits of project
        """
        limits = {}
        for limit in self.nova.limits.get().absolute:
            if limit.name == 'maxTotalCores':
                limits['vcpu'] = limit.value
            elif limit.name == 'maxTotalRAMSize':
                # change MB to GB
                limits['ram'] = round(limit.value / 1000.0, 1)
            elif limit.name == 'maxTotalInstances':
                limits['instances'] = limit.value

        neutron_limits = self.neutron.show_quota(tenant_id)['quota']
        limits['network'] = neutron_limits['network']
        limits['subnet'] = neutron_limits['subnet']
        limits['port'] = neutron_limits['port']

        return Limits(**limits)

    def get_terraform_provider(self) -> str:
        """
        Get OpenStack Terraform provider
        :return: Terraform provider template
        :raise InvalidTopologyDefinition: Terraform provider template is incorrect
        """
        try:
            template = self.template_environment.get_template(TERRAFORM_PROVIDER_TEMPLATE_FILE)
            return str(
                template.render(
                    auth_url=self.auth_url,
                    app_cred_id=self.app_cred_id,
                    app_cred_secret=self.app_cred_secret,
                )
            )
        except Exception as e:
            raise InvalidTopologyDefinition('Error while generating provider template: ', e) from e

    def validate_and_get_terraform_template(
        self,
        topology_instance: TopologyInstance,
        key_pair_name_ssh: str = 'dummy-ssh-key-pair',
        key_pair_name_cert: str = 'ummy-cert-key-pair',
        resource_prefix: str | None = None,
    ) -> str:
        """
        Transform TopologyInstance into Terraform Template.

        :param topology_instance: TopologyInstance used to create template
        :param key_pair_name_ssh: The name of a SSH key pair saved in the cloud
        :param key_pair_name_cert: The name of certificate key pair in the cloud
        :param resource_prefix: The prefix of all resources. None renders a template that is
            only validated, with placeholder names and tunnel ids.
        :return: Terraform Template as a string
        :raise: CrczpException on network validation error or a stack name without a sandbox id
        :raise: InvalidTopologyDefinition on an invalid network_forwarding rule or template
            rendering error
        """
        network_forwarding = topology_instance.get_network_forwarding()
        validate_destination_network(network_forwarding, topology_instance)
        tap_mirrors: list[TapMirror] = []
        if network_forwarding:
            base = 0 if resource_prefix is None else tunnel_id_base(resource_prefix)
            tap_mirrors = build_tap_mirrors(network_forwarding, base)
        if resource_prefix is None:
            resource_prefix = VALIDATION_RESOURCE_PREFIX
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
                network_forwarding=network_forwarding,
                tap_mirrors=tap_mirrors,
            )
        except Exception as e:
            raise InvalidTopologyDefinition('Error while generating template: ', e) from e

        return str(template_str)

    def _list_neutron_resources(
        self, kind: str, items: list[dict[str, Any]], name_key: str = 'name'
    ) -> list[TaggedResource]:
        return [
            TaggedResource(
                CloudResource(kind, item['id'], item.get(name_key) or ''),
                tuple(item.get('tags', ())),
            )
            for item in items
        ]

    def _list_tap_mirrors(self) -> list[dict[str, Any]]:
        try:
            return list(self.neutron.list('tap_mirrors', TAP_MIRRORS_PATH)['tap_mirrors'])
        except NeutronNotFound:
            return []

    def _discover_resources(self, servers: list[Any]) -> list[TaggedResource]:
        """
        List every server, network, subnet, port, router, floating IP and tap mirror of the
        project with the tags Neutron reports for it. Nova servers carry no tags here.
        """
        neutron = self.neutron
        return [
            *(TaggedResource(CloudResource(SERVER, server.id, server.name)) for server in servers),
            *self._list_neutron_resources(NETWORK, neutron.list_networks()['networks']),
            *self._list_neutron_resources(SUBNET, neutron.list_subnets()['subnets']),
            *self._list_neutron_resources(PORT, neutron.list_ports()['ports']),
            *self._list_neutron_resources(ROUTER, neutron.list_routers()['routers']),
            *self._list_neutron_resources(
                FLOATING_IP, neutron.list_floatingips()['floatingips'], 'floating_ip_address'
            ),
            *self._list_neutron_resources(TAP_MIRROR, self._list_tap_mirrors()),
        ]

    def _get_server_ram_gb(self, servers: list[Any]) -> dict[str, float]:
        if not servers:
            return {}
        flavor_ram_gb = {
            flavor.id: round(flavor.ram / 1000.0, 1) for flavor in self.nova.flavors.list()
        }
        return {server.id: flavor_ram_gb.get(server.flavor['id'], 0.0) for server in servers}

    def list_stack_inventories(self, stack_names: StackNameScheme) -> dict[str, StackInventory]:
        """
        List the stacks of one deployment that have resources in the project.

        :param stack_names: The stack names of the deployment
        :return: Resources and the RAM in GB held by the servers, keyed by full stack name
        """
        servers = list(self.nova.servers.list())
        return group_by_stack(
            self._discover_resources(servers), self._get_server_ram_gb(servers), stack_names
        )

    def delete_stack_resources(
        self, stack_name: str, stack_names: StackNameScheme
    ) -> list[ResourceDeletion]:
        """
        Delete the resources of one stack, dependents first.

        :param stack_name: The full stack name
        :param stack_names: The stack names of the deployment
        :return: The result of deleting each resource, in deletion order
        :raise ValueError: The name is not a full stack name of the deployment
        """
        stack_names.require(stack_name)
        resources = select_stack(
            self._discover_resources(list(self.nova.servers.list())), stack_name, stack_names
        )
        return delete_in_order(resources, self._resource_kinds(), _is_not_found)

    def _resource_kinds(self) -> tuple[ResourceKind, ...]:
        """The kinds of stack resources, dependents first, with how each is deleted."""
        return (
            ResourceKind(
                TAP_MIRROR,
                lambda mirror_id: self.neutron.delete(f'{TAP_MIRRORS_PATH}/{mirror_id}'),
            ),
            ResourceKind(FLOATING_IP, self.neutron.delete_floatingip),
            ResourceKind(SERVER, self.nova.servers.delete, self._await_servers_gone),
            ResourceKind(ROUTER, self._delete_router),
            ResourceKind(PORT, self.neutron.delete_port),
            ResourceKind(SUBNET, self.neutron.delete_subnet),
            ResourceKind(NETWORK, self.neutron.delete_network),
        )

    def _delete_router(self, router_id: str) -> None:
        """Detach the router's interfaces and gateway, then delete it."""
        for port in self.neutron.list_ports(device_id=router_id)['ports']:
            if port['device_owner'].startswith('network:router_interface'):
                with suppress(NeutronNotFound):
                    self.neutron.remove_interface_router(router_id, {'port_id': port['id']})
        self.neutron.update_router(router_id, {'router': {'external_gateway_info': {}}})
        self.neutron.delete_router(router_id)

    def _await_servers_gone(self, servers: Sequence[CloudResource]) -> dict[str, str]:
        """
        Wait until Nova has removed the servers, so that their ports can be deleted.

        :return: An error per ID of a server still present when the wait ends
        """
        pending = {server.resource_id for server in servers}
        deadline = time.monotonic() + SERVER_DELETION_TIMEOUT_SECONDS
        while pending and time.monotonic() < deadline:
            for server_id in list(pending):
                try:
                    self.nova.servers.get(server_id)
                except NovaNotFound:
                    pending.discard(server_id)
                except NovaClientException as error:
                    LOG.warning('Server lookup failed', server_id=server_id, error=str(error))
            if pending:
                time.sleep(SERVER_DELETION_POLL_SECONDS)
        return {
            server_id: f'Still present after {SERVER_DELETION_TIMEOUT_SECONDS} seconds'
            for server_id in pending
        }


def _is_not_found(error: Exception) -> bool:
    """Whether a Nova or Neutron error says the resource does not exist."""
    return isinstance(error, (NeutronNotFound, NovaNotFound))
