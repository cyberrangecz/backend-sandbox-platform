"""
OpenStackInstance manipulation functions.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any, TypedDict, cast

import structlog
from jinja2 import Environment, FileSystemLoader, select_autoescape
from openstack import exceptions as sdk_exceptions

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
    from openstack import connection
    from openstack.compute.v2 import flavor as sdk_flavor
    from openstack.compute.v2.server import Server
    from openstack.image.v2 import _proxy as image_v2_proxy
    from openstack.image.v2 import image as sdk_image
    from openstack.network.v2 import quota as network_quota
    from openstack.resource import Resource

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
SERVER_DELETION_TIMEOUT_SECONDS = 300
SERVER_DELETION_POLL_SECONDS = 2

REBOOT_TYPE = 'SOFT'
# The console types Nova serves at the pinned compute microversion (webmks needs 2.8). An
# unknown type is rejected before any request; the SDK would send it with a null protocol.
SUPPORTED_CONSOLE_TYPES = frozenset({
    'novnc',
    'xvpvnc',
    'spice-html5',
    'rdp-html5',
    'serial',
    'webmks',
})


def regex_replace(string: str, pattern: str = '', replace: str = '') -> str:
    """Apply a regex substitution on *string* (used as a Jinja2 filter)."""
    return re.sub(pattern, replace, string)


def _nova_str(e: sdk_exceptions.SDKException) -> str:
    """
    Renders an SDK error as '<message> (HTTP <code>) (Request-ID: <id>)'.

    The server's message is in `details`. str(e) is not used, because it also carries the
    internal endpoint URL, which would end up in API error responses.
    """
    if isinstance(e, sdk_exceptions.HttpException):
        text = f'{e.details or e.message} (HTTP {e.status_code})'
        if e.request_id:
            text += f' (Request-ID: {e.request_id})'
        return text
    return e.message


def _nova_message(e: Exception) -> str:
    """Returns only the message of an SDK error, without the HTTP status and request ID."""
    if isinstance(e, sdk_exceptions.HttpException):
        return e.details or e.message
    if isinstance(e, sdk_exceptions.SDKException):
        return e.message
    return str(e)


class FlavorUsage(TypedDict):
    """Hardware consumed by a single OpenStack flavor: whole vCPUs and RAM in GB."""

    vcpu: int
    ram: float


class OpenStackProxy:
    """
    Used for work with instances of virtual machines in openstack.

    Uses the OpenStack compute (nova), image (glance) and network (neutron) services through
    an openstacksdk connection.
    """

    def __init__(
        self,
        conn: connection.Connection,
        auth_url: str,
        app_cred_id: str,
        app_cred_secret: str,
        trc: TransformationConfiguration,
    ) -> None:
        # The first access to conn.compute, conn.image or conn.network authenticates and
        # discovers the service, so it must not happen here, outside @check_authentication.
        self.conn = conn
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

    @property
    def _image(self) -> image_v2_proxy.Proxy:
        """The image service proxy, which get_connection pins to the v2 API."""
        return cast('image_v2_proxy.Proxy', self.conn.image)

    @staticmethod
    def _get_owner_specified_data(properties: Mapping[str, Any]) -> dict[str, str]:
        """
        Creates a dictionary of owner specified data from the image

        :param properties: Custom properties of the image
        :return: Dictionary with arbitrary number of owner_specified values from the image
        """
        return {k: v for (k, v) in properties.items() if k.startswith('owner_specified.')}

    def _to_image(self, image: sdk_image.Image) -> Image:
        """
        Converts an image from glance to an Image object.

        :param image: Image from the image service
        :return: Image
        """
        # Keys that are not standard image attributes, such as default_user and the
        # owner_specified.* ones, are only in `properties`, which is None without them.
        properties = image.properties or {}
        return Image(
            os_distro=image.os_distro,
            os_type=image.os_type,
            disk_format=image.disk_format,
            container_format=image.container_format,
            visibility=image.visibility,
            size=image.size,
            status=image.status,
            min_ram=image.min_ram,
            min_disk=image.min_disk,
            created_at=image.created_at,
            updated_at=image.updated_at,
            tags=image.tags,
            default_user=properties.get('default_user'),
            name=image.name,
            owner_specified=self._get_owner_specified_data(properties),
        )

    def list_images(self) -> list[Image]:
        """
        Gets image list

        :return: List of Image objects.
        """
        # No `limit`: with one, the SDK ends every listing with a request for the page after
        # the last image, which fails if that image is deleted in between. Without one it
        # only follows the `next` links of glance.
        return [self._to_image(image) for image in self._image.images()]

    def get_keypair(self, keypair_name: str) -> dict[str, Any]:
        """
        Gets keypair from nova
        :param keypair_name: name of keypair to get
        :return: Keypair as a dict with Nova's keys
        """
        try:
            keypair = self.conn.compute.get_keypair(keypair_name)
        except sdk_exceptions.SDKException as e:
            raise CrczpException(f"Failed to get keypair '{keypair_name}': {_nova_str(e)}") from e
        return {
            'name': keypair.name,
            'public_key': keypair.public_key,
            'fingerprint': keypair.fingerprint,
            'type': keypair.type,
            'user_id': keypair.user_id,
            'created_at': keypair.created_at,
        }

    def create_keypair(self, name: str, public_key: str | None, key_type: str) -> None:
        """
        Create key-pair in nova.

        :param name: Name of ssh key in stack.
        :param public_key: key to be stored, if None new is created
        :param key_type: type of public key from which keypair is created,
                         accepted values are 'ssh' and 'x509'
        :return: None
        """
        attrs: dict[str, Any] = {'name': name, 'type': key_type}
        # Leave out a missing key, so that nova generates one: an explicit null is rejected.
        if public_key:
            attrs['public_key'] = public_key
        try:
            self.conn.compute.create_keypair(**attrs)
        except sdk_exceptions.SDKException as e:
            msg = (
                f"Failed to create keypair '{name}' with public key '{public_key}': {_nova_str(e)}"
            )
            raise CrczpException(msg) from e

    def delete_keypair(self, name: str) -> None:
        """
        Delete keypair in OpenStack.

        :param name: name of key to be deleted.
        :return: None
        :raise CrczpException: Keypair not found
        """
        try:
            # The SDK ignores a missing keypair by default; callers rely on the error.
            self.conn.compute.delete_keypair(name, ignore_missing=False)
        except sdk_exceptions.SDKException as e:
            raise CrczpException(f"Failed to delete keypair '{name}': {_nova_str(e)}") from e

    def resume_instance(self, node_id: str) -> None:
        """
        Resume instance.

        :param node_id: The ID of the node
        :return: None
        :raise CrczpException: Node not found
        """
        try:
            self.conn.compute.resume_server(node_id)
        except sdk_exceptions.SDKException as e:
            msg = f'Failed to resume instance with id={node_id}: {_nova_str(e)}.'
            raise CrczpException(msg) from e

    def start_instance(self, node_id: str) -> None:
        """
        Start instance.

        :param node_id: The ID of the node
        :return: None
        :raise CrczpException: Node not found
        """
        try:
            self.conn.compute.start_server(node_id)
        except sdk_exceptions.SDKException as e:
            msg = f'Failed to start instance with id={node_id}: {_nova_str(e)}.'
            raise CrczpException(msg) from e

    def reboot_instance(self, node_id: str) -> None:
        """
        Reboot instance.

        :param node_id: The ID of the node
        :return: None
        :raise CrczpException: Node not found
        """
        try:
            self.conn.compute.reboot_server(node_id, REBOOT_TYPE)
        except sdk_exceptions.SDKException as e:
            msg = f'Failed to reboot instance with id={node_id}: {_nova_str(e)}.'
            raise CrczpException(msg) from e

    def get_image(self, image_id: str) -> Image:
        """
        Gets an Image object containing its details based on its id.

        :param image_id: id of the image to retrieve
        :return: Image
        """
        return self._to_image(self._image.get_image(image_id))

    def get_console_url(self, node_id: str, console_type: str) -> str:
        """
        Get console for given node.

        :param node_id: The ID of the node
        :param console_type: Type can be novnc, xvpvnc, spice-html5, rdp-html5, serial and webmks
        :return: Console url
        :raise StackException: Node not found or the console type is not supported
        """
        if console_type not in SUPPORTED_CONSOLE_TYPES:
            raise StackException(
                f'OpenStack error while getting console: Unsupported console_type "{console_type}"'
            )
        try:
            console = self.conn.compute.create_server_remote_console(node_id, type=console_type)
        # The SDK raises a plain ValueError for a console type the server's microversion lacks.
        except (sdk_exceptions.SDKException, ValueError) as e:
            raise StackException(
                f'OpenStack error while getting console: {_nova_message(e)}'
            ) from e

        return str(console.url)

    def get_quota_set(self, tenant_id: str) -> QuotaSet:
        """
        Get quota set of OpenStack project.

        :param tenant_id: ID of OpenStack project.
        :return QuotaSet object
        """
        # Each quota attribute holds the limit; the amount in use is in `usage`.
        nova_quotas = self.conn.compute.get_quota_set(tenant_id, usage=True)
        vcpu = Quota(nova_quotas.cores, nova_quotas.usage['cores'])
        # change MB to GB
        ram = Quota(round(nova_quotas.ram / 1000.0, 1), round(nova_quotas.usage['ram'] / 1000.0, 1))
        instances = Quota(nova_quotas.instances, nova_quotas.usage['instances'])

        neutron_quotas = cast(
            'network_quota.QuotaDetails', self.conn.network.get_quota(tenant_id, details=True)
        )
        network = Quota(neutron_quotas.networks['limit'], neutron_quotas.networks['used'])
        subnet = Quota(neutron_quotas.subnets['limit'], neutron_quotas.subnets['used'])
        port = Quota(neutron_quotas.ports['limit'], neutron_quotas.ports['used'])

        return QuotaSet(vcpu, ram, instances, network, subnet, port)

    @staticmethod
    def _get_flavors_dict(flavors: Iterable[sdk_flavor.Flavor]) -> dict[str, FlavorUsage]:
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
        # Without is_public, the SDK asks for every flavor, which for an admin includes the
        # private flavors of other projects. True means public plus the project's own.
        return self._get_flavors_dict(self.conn.compute.flavors(is_public=True))

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
        # Nova's limits apply to the project the token is scoped to.
        absolute = self.conn.compute.get_limits().absolute
        limits: dict[str, Any] = {}
        if absolute.total_cores is not None:
            limits['vcpu'] = absolute.total_cores
        if absolute.total_ram is not None:
            # change MB to GB
            limits['ram'] = round(absolute.total_ram / 1000.0, 1)
        if absolute.instances is not None:
            limits['instances'] = absolute.instances

        neutron_limits = self.conn.network.get_quota(tenant_id)
        limits['network'] = neutron_limits.networks
        limits['subnet'] = neutron_limits.subnets
        limits['port'] = neutron_limits.ports

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

    def _list_tap_mirrors(self) -> list[Resource]:
        """The project's tap mirrors; none on a cloud without the TaaS extension."""
        try:
            return list(self.conn.network.tap_mirrors())
        except sdk_exceptions.NotFoundException:
            return []

    def _discover_resources(self, servers: list[Server]) -> list[TaggedResource]:
        """
        List every server, network, subnet, port, router, floating IP and tap mirror of the
        project with the tags Neutron reports for it. Nova servers carry no tags here.
        """
        network = self.conn.network
        return [
            *(
                TaggedResource(CloudResource(SERVER, server.id, server.name or ''))
                for server in servers
            ),
            *_tagged(NETWORK, network.networks()),
            *_tagged(SUBNET, network.subnets()),
            *_tagged(PORT, network.ports()),
            *_tagged(ROUTER, network.routers()),
            *_tagged(FLOATING_IP, network.ips(), 'floating_ip_address'),
            *_tagged(TAP_MIRROR, self._list_tap_mirrors()),
        ]

    def _get_server_ram_gb(self, servers: list[Server]) -> dict[str, float]:
        if not servers:
            return {}
        flavor_ram_gb = {
            flavor.id: round(flavor.ram / 1000.0, 1)
            for flavor in self.conn.compute.flavors(is_public=True)
        }
        # At the pinned compute microversion a server's flavor carries the flavor's ID.
        return {server.id: flavor_ram_gb.get(server.flavor.id, 0.0) for server in servers}

    def list_stack_inventories(self, stack_names: StackNameScheme) -> dict[str, StackInventory]:
        """
        List the stacks of one deployment that have resources in the project.

        :param stack_names: The stack names of the deployment
        :return: Resources and the RAM in GB held by the servers, keyed by full stack name
        """
        servers = list(self.conn.compute.servers())
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
            self._discover_resources(list(self.conn.compute.servers())), stack_name, stack_names
        )
        return delete_in_order(resources, self._resource_kinds(), _is_not_found)

    def _resource_kinds(self) -> tuple[ResourceKind, ...]:
        """The kinds of stack resources, dependents first, with how each is deleted."""
        network, compute = self.conn.network, self.conn.compute
        # The SDK ignores a missing resource unless told otherwise; it is reported as gone.
        return (
            ResourceKind(TAP_MIRROR, _api_errors(network.delete_tap_mirror)),
            ResourceKind(FLOATING_IP, _api_errors(network.delete_ip)),
            ResourceKind(SERVER, _api_errors(compute.delete_server), self._await_servers_gone),
            ResourceKind(ROUTER, _api_errors(self._delete_router)),
            ResourceKind(PORT, _api_errors(network.delete_port)),
            ResourceKind(SUBNET, _api_errors(network.delete_subnet)),
            ResourceKind(NETWORK, _api_errors(network.delete_network)),
        )

    def _delete_router(self, router_id: str, ignore_missing: bool = False) -> None:
        """Detach the router's interfaces and gateway, then delete it."""
        network = self.conn.network
        for port in network.ports(device_id=router_id):
            if (port.device_owner or '').startswith('network:router_interface'):
                with suppress(sdk_exceptions.NotFoundException):
                    network.remove_interface_from_router(router_id, port=port.id)
        network.update_router(router_id, external_gateway_info={})
        network.delete_router(router_id, ignore_missing=ignore_missing)

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
                    self.conn.compute.get_server(server_id)
                except sdk_exceptions.NotFoundException:
                    pending.discard(server_id)
                except sdk_exceptions.SDKException as error:
                    LOG.warning('Server lookup failed', server_id=server_id, error=_nova_str(error))
            if pending:
                time.sleep(SERVER_DELETION_POLL_SECONDS)
        return {
            server_id: f'Still present after {SERVER_DELETION_TIMEOUT_SECONDS} seconds'
            for server_id in pending
        }


def _tagged(
    kind: str, resources: Iterable[Resource], name_attribute: str = 'name'
) -> list[TaggedResource]:
    """The resources of one kind, each with the tags Neutron sends for it, if any."""
    return [
        TaggedResource(
            CloudResource(kind, resource.id, getattr(resource, name_attribute) or ''),
            tuple(getattr(resource, 'tags', None) or ()),
        )
        for resource in resources
    ]


def _api_errors(delete: Callable[..., object]) -> Callable[[str], object]:
    """
    Delete a resource by its ID, reporting a missing one as NotFoundException.

    Any other failed API call becomes a CrczpException with the service's message, status and
    request ID: str() of an SDK error also names the internal endpoint URL, and the failures
    end up in stage error messages that the API returns.
    """

    def call(resource_id: str) -> object:
        try:
            return delete(resource_id, ignore_missing=False)
        except sdk_exceptions.NotFoundException:
            raise
        except sdk_exceptions.SDKException as error:
            raise CrczpException(_nova_str(error)) from error

    return call


def _is_not_found(error: Exception) -> bool:
    """Whether a Nova or Neutron error says the resource does not exist."""
    return isinstance(error, sdk_exceptions.NotFoundException)
