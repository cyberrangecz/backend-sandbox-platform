"""Tests for crczp.openstack_driver.open_stack_proxy module."""

import re

import pytest
from novaclient.exceptions import ClientException as NovaClientException
from ruamel.yaml import YAML

from crczp.cloud_commons import (
    CrczpException,
    HardwareUsage,
    Image,
    Limits,
    Quota,
    QuotaSet,
    StackException,
)
from crczp.openstack_driver import network_forwarding, utils
from crczp.openstack_driver.network_forwarding import build_tap_mirror_plan
from crczp.openstack_driver.open_stack_proxy import OpenStackProxy


def _resource_block(template: str, resource_type: str, name: str) -> str:
    """Return the body of a single Terraform resource block."""
    start = template.index(f'resource "{resource_type}" "{name}" {{')
    return template[start : template.index('\n}', start)]


# OpenTofu rejects `}data "x" "y" {` with "Missing newline after block definition", and a
# single Jinja whitespace-control marker is enough to produce it.
_BLOCK_WELD = re.compile(r'^\s*\}\s*\S')


def _assert_blocks_newline_separated(template: str) -> None:
    """Assert every block in a rendered template closes on a line of its own."""
    welded = [
        (n, line) for n, line in enumerate(template.splitlines(), 1) if _BLOCK_WELD.match(line)
    ]
    assert not welded, f'block definition not terminated by a newline: {welded}'


class TestOpenStackProxy:  # pylint: disable=too-many-public-methods
    """Unit tests for the OpenStackProxy class."""

    @pytest.fixture()
    def open_stack_proxy(self, mocker, trc):
        """Create an OpenStackProxy instance backed by mock clients."""
        mock_session = mocker.MagicMock()
        auth_url = 'url'
        application_credential_id = 'app_id'
        application_credential_secret = 'app_secret'

        glance_client = utils.get_client('glance', mock_session)
        nova_client = utils.get_client('nova', mock_session)
        neutron_client = utils.get_client('neutron', mock_session)

        open_stack_proxy = OpenStackProxy(
            glance_client,
            nova_client,
            neutron_client,
            auth_url,
            application_credential_id,
            application_credential_secret,
            trc,
            hypervisor_cidr='10.99.0.0/16',
        )

        return open_stack_proxy

    @pytest.fixture()
    def stack_resources(self):
        """Return a sample stack resources dict."""
        resources = {
            'physical_resource_id': 'node_id',
            'creation_time': '22',
            'updated_time': '33',
            'attributes': {
                'status': 'ACTIVE',
                'flavor': {'original_name': 'name'},
                'image': {'id': 2},
                'addresses': {'network_name': [{'addr': '10.10.10.2'}]},
            },
        }

        return resources

    @pytest.fixture()
    def mock_get_quota_set(self, mocker, open_stack_proxy):
        """Return a helper that stubs get_quota_set on the proxy."""

        def get_quota_set(quotas):
            quota_set = QuotaSet(**quotas)
            open_stack_proxy.get_quota_set = mocker.MagicMock()
            open_stack_proxy.get_quota_set.return_value = quota_set
            return quota_set

        return get_quota_set

    @pytest.fixture()
    def mock_flavors(self, mocker, open_stack_proxy):
        """Stub the nova flavors list and _get_flavors_dict on the proxy."""
        open_stack_proxy.nova.flavors = mocker.MagicMock()

        flavors = {'standard.small': {'vcpu': 1, 'ram': 2}}
        open_stack_proxy._get_flavors_dict = mocker.MagicMock()  # pylint: disable=protected-access
        open_stack_proxy._get_flavors_dict.return_value = flavors  # pylint: disable=protected-access

    @pytest.fixture()
    def quotas(self):
        """Return a dict of Quota objects for all quota dimensions."""
        quota = Quota(100, 0)
        return {
            'vcpu': quota,
            'ram': quota,
            'instances': quota,
            'network': quota,
            'subnet': quota,
            'port': quota,
        }

    @pytest.fixture()
    def expected_hardware_usage(self):
        """Return the expected HardwareUsage for the test topology."""
        return HardwareUsage(5, 10, 5, 4, 4, 12)

    @pytest.mark.parametrize(
        'image_parameters',
        [
            {
                'os_distro': 'os_distro',
                'os_type': 'os_type',
                'disk_format': 'disk_format',
                'container_format': 'container_format',
                'visibility': 'visibility',
                'size': 'size',
                'status': 'status',
                'min_ram': 'min_ram',
                'min_disk': 'min_disk',
                'created_at': 'created_at',
                'updated_at': 'updated_at',
                'tags': 'tags',
                'default_user': 'default_user',
                'name': 'image_name',
                'owner_specified.openstack.version': '4.2.0',
                'owner_specified.openstack.md5': 'cdfb2e4052f2d6d97b0001ee998909a7',
                'owner_specified.openstack.sha256': '0463ac656ddbc771fda81a428b2eb',
                'owner_specified.openstack.object': 'images/debian-10-4.2.0',
            }
        ],
    )
    @pytest.mark.parametrize(
        'image_object',
        [
            {
                'os_distro': 'os_distro',
                'os_type': 'os_type',
                'disk_format': 'disk_format',
                'container_format': 'container_format',
                'visibility': 'visibility',
                'size': 'size',
                'status': 'status',
                'min_ram': 'min_ram',
                'min_disk': 'min_disk',
                'created_at': 'created_at',
                'updated_at': 'updated_at',
                'tags': 'tags',
                'default_user': 'default_user',
                'name': 'image_name',
                'owner_specified': {
                    'owner_specified.openstack.version': '4.2.0',
                    'owner_specified.openstack.md5': 'cdfb2e4052f2d6d97b0001ee998909a7',
                    'owner_specified.openstack.sha256': '0463ac656ddbc771fda81a428b2eb',
                    'owner_specified.openstack.object': 'images/debian-10-4.2.0',
                },
            }
        ],
    )
    def test_image_list(self, mocker, open_stack_proxy, image_parameters, image_object):
        """Test that list_images returns correctly mapped Image objects."""
        expected_image = Image(**image_object)

        open_stack_proxy.glance.images.list = mocker.MagicMock()
        open_stack_proxy.glance.images.list.return_value = [image_parameters]

        result = open_stack_proxy.list_images()

        assert result[0] == expected_image

    def test_get_keypair(self, mocker, open_stack_proxy):
        """Test that get_keypair returns the nova keypair."""
        open_stack_proxy.nova.keypairs.get = mocker.MagicMock()
        open_stack_proxy.nova.keypairs.get.return_value = 'testKey'

        result = open_stack_proxy.get_keypair('keypair')

        assert result == 'testKey'
        open_stack_proxy.nova.keypairs.get.assert_called_with('keypair')

    def test_get_keypair_nonexistent_keypair(self, mocker, open_stack_proxy):
        """Test that get_keypair raises CrczpException when the keypair does not exist."""
        open_stack_proxy.nova.keypairs.get = mocker.MagicMock()
        open_stack_proxy.nova.keypairs.get.side_effect = NovaClientException('testException')

        with pytest.raises(CrczpException):
            open_stack_proxy.get_keypair('keypair')

    def test_create_keypair(self, mocker, open_stack_proxy):
        """Test that create_keypair calls nova and returns the created keypair."""
        open_stack_proxy.nova.keypairs.create = mocker.MagicMock()
        open_stack_proxy.nova.keypairs.create.return_value = 'testKeyPair'

        result = open_stack_proxy.create_keypair('keypair', 'public_key', 'ssh')

        assert result == 'testKeyPair'
        open_stack_proxy.nova.keypairs.create.assert_called_with(
            'keypair', 'public_key', key_type='ssh'
        )

    def test_create_keypair_already_exist(self, mocker, open_stack_proxy):
        """Test that create_keypair raises CrczpException when the keypair already exists."""
        open_stack_proxy.nova.keypairs.create = mocker.MagicMock()
        open_stack_proxy.nova.keypairs.create.side_effect = NovaClientException('testException')

        with pytest.raises(CrczpException):
            open_stack_proxy.create_keypair('key', 'public_key', 'ssh')

    def test_delete_keypair(self, mocker, open_stack_proxy):
        """Test that delete_keypair calls nova and returns the result."""
        open_stack_proxy.nova.keypairs.delete = mocker.MagicMock()
        open_stack_proxy.nova.keypairs.delete.return_value = 'keyDeleted'

        result = open_stack_proxy.delete_keypair('keypair')

        assert result == 'keyDeleted'
        open_stack_proxy.nova.keypairs.delete.assert_called_with('keypair')

    def test_delete_keypair_nonexistent_keypair(self, mocker, open_stack_proxy):
        """Test that delete_keypair raises CrczpException when the keypair does not exist."""
        open_stack_proxy.nova.keypairs.delete = mocker.MagicMock()
        open_stack_proxy.nova.keypairs.delete.side_effect = NovaClientException('testException')

        with pytest.raises(CrczpException):
            open_stack_proxy.delete_keypair('key')

    def test_instance_reboot(self, mocker, open_stack_proxy):
        """Test that reboot_instance calls the nova reboot endpoint."""
        open_stack_proxy.nova.servers.reboot = mocker.MagicMock()
        open_stack_proxy.reboot_instance('node_id')

        open_stack_proxy.nova.servers.reboot.assert_called_with('node_id')

    def test_instance_reboot_stack_or_instance_not_found(self, mocker, open_stack_proxy):
        """Test that reboot_instance raises CrczpException when the instance is not found."""
        open_stack_proxy.nova.servers.reboot = mocker.MagicMock()
        open_stack_proxy.nova.servers.reboot.side_effect = NovaClientException('testException')

        with pytest.raises(CrczpException):
            open_stack_proxy.reboot_instance('node_id')

    def test_instance_start(self, mocker, open_stack_proxy):
        """Test that start_instance calls the nova start endpoint."""
        open_stack_proxy.nova.servers.start = mocker.MagicMock()
        open_stack_proxy.start_instance('node_id')

        open_stack_proxy.nova.servers.start.assert_called_with('node_id')

    def test_instance_start_stack_or_instance_not_found(self, mocker, open_stack_proxy):
        """Test that start_instance raises CrczpException when the instance is not found."""
        open_stack_proxy.nova.servers.start = mocker.MagicMock()
        open_stack_proxy.nova.servers.start.side_effect = NovaClientException('testException')

        with pytest.raises(CrczpException):
            open_stack_proxy.start_instance('node_id')

    def test_instance_resume(self, mocker, open_stack_proxy):
        """Test that resume_instance calls the nova resume endpoint."""
        open_stack_proxy.nova.servers.resume = mocker.MagicMock()
        open_stack_proxy.resume_instance('node_id')

        open_stack_proxy.nova.servers.resume.assert_called_with('node_id')

    def test_instance_resume_stack_or_instance_not_found(self, mocker, open_stack_proxy):
        """Test that resume_instance raises CrczpException when the instance is not found."""
        open_stack_proxy.nova.servers.resume = mocker.MagicMock()
        open_stack_proxy.nova.servers.resume.side_effect = NovaClientException('testException')

        with pytest.raises(CrczpException):
            open_stack_proxy.resume_instance('node_id')

    def test_get_console_url(self, mocker, open_stack_proxy, stack_resources):
        """Test that get_console_url returns the expected console URL."""
        expected_url = 'https://console.example.com/spice'
        open_stack_proxy.nova.servers.get_console_url = mocker.MagicMock(
            return_value={'remote_console': {'url': expected_url}}
        )

        result = open_stack_proxy.get_console_url('node_id', 'spice-html5')

        assert result == expected_url
        open_stack_proxy.nova.servers.get_console_url.assert_called_with(
            stack_resources['physical_resource_id'], 'spice-html5'
        )

    def test_get_console_url_instance_or_stack_not_found(self, mocker, open_stack_proxy):
        """Test that get_console_url raises StackException when the instance is not found."""
        open_stack_proxy.nova.servers.get_console_url = mocker.MagicMock()
        exc = NovaClientException('testException')
        open_stack_proxy.nova.servers.get_console_url.side_effect = exc

        with pytest.raises(StackException):
            open_stack_proxy.get_console_url('node_id', 'spice-html5')

    def test_get_quota_set(self, mocker, open_stack_proxy):
        """Test that get_quota_set returns a correctly populated QuotaSet."""
        custom_quota_set = mocker.MagicMock()
        custom_quota_set.cores = {'limit': 111, 'in_use': 100}
        custom_quota_set.ram = {'limit': 50500, 'in_use': 1500}
        custom_quota_set.instances = {'limit': 222, 'in_use': 200}

        custom_network_quotas = {
            'quota': {
                'network': {'limit': 333, 'used': 300},
                'subnet': {'limit': 444, 'used': 400},
                'port': {'limit': 555, 'used': 500},
            }
        }

        expected_quota_set = QuotaSet(
            Quota(111, 100),
            Quota(50.5, 1.5),
            Quota(222, 200),
            Quota(333, 300),
            Quota(444, 400),
            Quota(555, 500),
        )

        open_stack_proxy.nova.quotas.get = mocker.MagicMock()
        open_stack_proxy.nova.quotas.get.return_value = custom_quota_set

        open_stack_proxy.neutron.show_quota_details = mocker.MagicMock()
        open_stack_proxy.neutron.show_quota_details.return_value = custom_network_quotas

        result_quota_set = open_stack_proxy.get_quota_set('tenant_id')

        assert result_quota_set == expected_quota_set

    def test_get_flavors_dict(self, mocker):
        """Test that _get_flavors_dict maps flavor objects to the expected dict structure."""
        flavor = mocker.MagicMock()
        flavor.name = 'test_flavor'
        flavor.vcpus = 1
        flavor.ram = 2000

        result = OpenStackProxy._get_flavors_dict([flavor])  # pylint: disable=protected-access

        assert result.get('test_flavor')
        assert result['test_flavor'].get('vcpu') == 1
        assert result['test_flavor'].get('ram') == 2

    def test_get_hardware_usage(
        self, open_stack_proxy, topology_instance, expected_hardware_usage, mock_flavors
    ):
        """Test that get_hardware_usage returns the correct HardwareUsage for a topology."""
        result = open_stack_proxy.get_hardware_usage(topology_instance)

        assert expected_hardware_usage == result

    def test_get_project_limits(self, mocker, open_stack_proxy):
        """Test that get_project_limits returns correct Limits from nova and neutron."""
        expected_limits_dict = {
            'vcpu': 10,
            'ram': 10.0,
            'instances': 10,
            'network': 10,
            'subnet': 10,
            'port': 10,
        }

        total_cores = mocker.MagicMock()
        total_cores.name = 'maxTotalCores'
        total_cores.value = expected_limits_dict['vcpu']
        total_ram = mocker.MagicMock()
        total_ram.name = 'maxTotalRAMSize'
        total_ram.value = expected_limits_dict['ram'] * 1000
        total_instance = mocker.MagicMock()
        total_instance.name = 'maxTotalInstances'
        total_instance.value = expected_limits_dict['instances']

        nova_limits = mocker.MagicMock()
        nova_limits.absolute = [total_cores, total_ram, total_instance]
        open_stack_proxy.nova.limits.get = mocker.MagicMock()
        open_stack_proxy.nova.limits.get.return_value = nova_limits

        neutron_limits = {
            'quota': {
                'network': expected_limits_dict['network'],
                'subnet': expected_limits_dict['subnet'],
                'port': expected_limits_dict['port'],
            }
        }

        open_stack_proxy.neutron.show_quota = mocker.MagicMock()
        open_stack_proxy.neutron.show_quota.return_value = neutron_limits

        result_limits = open_stack_proxy.get_project_limits('tenant_id')
        expected_limits = Limits(**expected_limits_dict)

        assert result_limits.vcpu == expected_limits.vcpu
        assert result_limits.ram == expected_limits.ram
        assert result_limits.instances == expected_limits.instances

    def test_validate_and_get_terraform_template(
        self, open_stack_proxy, topology_instance, generated_terraform_template
    ):
        """Test that validate_and_get_terraform_template produces the expected YAML output."""
        template_str = open_stack_proxy.validate_and_get_terraform_template(topology_instance)
        yaml = YAML(typ='rt')
        template_dict = yaml.load(template_str)

        filtered = '\n'.join(s for s in str(template_dict).split('\n') if s)
        assert filtered == str(generated_terraform_template)

    def test_template_no_forwarding_omits_tap_mirror(self, open_stack_proxy, topology_instance):
        """A definition without network_forwarding emits no TaaS/FIP resources."""
        template_str = open_stack_proxy.validate_and_get_terraform_template(topology_instance)

        assert 'openstack_taas_tap_mirror_v2' not in template_str
        assert 'openstack_networking_floatingip_v2' not in template_str
        assert 'openstack_networking_router_v2' not in template_str
        assert 'resource "openstack_networking_secgroup_v2"' not in template_str

    def test_template_volumes_render_per_volume_images(
        self, open_stack_proxy, topology_instance_volumes
    ):
        """A host with volumes gets one block_device per volume; each may come from its own image.

        The 'server' host declares three volumes: a boot volume with no image (falls back to the
        host base_box image), an extra volume with its own image, and a blank extra volume.
        """
        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance_volumes
        )
        server = _resource_block(template_str, 'openstack_compute_instance_v2', 'stack-name-server')

        # A volume-backed instance boots from its block device, so no top-level image_name.
        assert 'image_name' not in server
        # Per-volume image data sources: boot volume falls back to base_box image, extra volume
        # uses its own image; the blank volume gets none.
        assert (
            'data "openstack_images_image_ids_v2" "image_data_source-stack-name-server-0" {\n'
            '  name = "debian-12-x86_64"' in template_str
        )
        assert (
            'data "openstack_images_image_ids_v2" "image_data_source-stack-name-server-1" {\n'
            '  name = "data-disk-x86_64"' in template_str
        )
        assert 'image_data_source-stack-name-server-2' not in template_str
        # No un-indexed data source for a volume-backed host.
        assert 'image_data_source-stack-name-server"' not in template_str

        # Three block devices: image boot disk, image extra disk, blank extra disk.
        assert server.count('block_device {') == 3
        assert 'boot_index            = 0' in server
        assert server.count('boot_index            = -1') == 2
        assert server.count('source_type           = "image"') == 2
        assert 'source_type           = "blank"' in server
        assert 'volume_size           = 20' in server
        assert 'volume_size           = 30' in server
        assert 'volume_size           = 40' in server

        # A host without volumes still boots directly from its image (unchanged behavior).
        home = _resource_block(template_str, 'openstack_compute_instance_v2', 'stack-name-home')
        assert 'image_name = "debian-12-x86_64"' in home
        assert 'block_device' not in home

    def test_template_forwarding_emits_tap_mirror(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """A definition with network_forwarding emits FIP plumbing and a tap_mirror."""
        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance_forwarding, resource_prefix='stack-p1-s2'
        )

        # Platform router auto-discovery from base_network (crczp-network) + external network.
        assert 'data "openstack_networking_port_v2" "network-forwarding-base-router-port"' in (
            template_str
        )
        assert 'device_owner = "network:router_interface"' in template_str
        assert 'data "openstack_networking_router_v2" "network-forwarding-platform-router"' in (
            template_str
        )

        # The sandbox gets its own router, gatewayed to the platform's external network.
        assert 'resource "openstack_networking_router_v2" "stack-p1-s2-tapm-rtr"' in template_str
        assert (
            'external_network_id = '
            'data.openstack_networking_router_v2.network-forwarding-platform-router'
            '.external_network_id'
        ) in template_str

        # FIP plumbing for the destination interface, attached to the sandbox's own router.
        # Names are asserted, not just resource types, so a plan/template drift cannot pass.
        assert (
            'resource "openstack_networking_router_interface_v2" '
            '"stack-p1-s2-tapm-ri-monitoring-switch"'
        ) in template_str
        assert (
            'resource "openstack_networking_port_v2" "stack-p1-s2-tapm-rp-monitoring-switch"'
        ) in template_str
        assert 'router_id = openstack_networking_router_v2.stack-p1-s2-tapm-rtr.id' in template_str
        assert 'router_id = data.openstack_networking_router_v2.' not in template_str
        # The destination interface name is auto-generated ("link-N"), so only the tag is pinned.
        assert (
            'resource "openstack_networking_floatingip_v2" "stack-p1-s2-tapm-fip-' in template_str
        )
        assert (
            'resource "openstack_networking_floatingip_associate_v2" "stack-p1-s2-tapm-fipa-'
        ) in template_str

        # The tap_mirror mirrors into the destination floating IP with derived tunnel ids.
        assert 'resource "openstack_taas_tap_mirror_v2" "stack-p1-s2-tapm-0"' in template_str
        assert 'mirror_type = "gre"' in template_str
        assert 'remote_ip   = openstack_networking_floatingip_v2.' in template_str
        assert '.address' in template_str
        # sandbox id 2 -> tunnel base 2 * 1024 = 2048; direction "both" -> in/out.
        assert 'in  = 2048' in template_str
        assert 'out = 2049' in template_str

    def test_template_forwarding_mirror_type_from_config(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """The proxy's configured mirror_type (not the topology) drives the encapsulation."""
        open_stack_proxy.mirror_type = 'erspanv1'

        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance_forwarding, resource_prefix='stack-p1-s2'
        )

        assert 'mirror_type = "erspanv1"' in template_str
        assert 'mirror_type = "gre"' not in template_str

    def test_template_forwarding_destination_port_uses_hypervisor_secgroup(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """The destination port carries the hypervisor-only group instead of the topology one."""
        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance_forwarding, resource_prefix='stack-p1-s2'
        )
        plan = build_tap_mirror_plan(
            topology_instance_forwarding.get_network_forwarding(), 'stack-p1-s2', 'gre'
        )
        (dest_port_name,) = plan.destination_port_names

        assert f'resource "openstack_networking_secgroup_v2" "{plan.secgroup_name}"' in template_str
        assert (
            f'resource "openstack_networking_secgroup_rule_v2" "{plan.secgroup_name}-ingress"'
        ) in template_str
        assert 'direction         = "ingress"' in template_str
        assert 'remote_ip_prefix  = "10.99.0.0/16"' in template_str

        # Security group rules are a union, so the destination port must not keep the topology
        # group as well — that one admits 0.0.0.0/0.
        dest_port = _resource_block(template_str, 'openstack_networking_port_v2', dest_port_name)
        assert f'openstack_networking_secgroup_v2.{plan.secgroup_name}.id' in dest_port
        assert 'sandbox-internal-sg' not in dest_port

        # Mirrored source ports are untouched.
        source_port = _resource_block(
            template_str, 'openstack_networking_port_v2', plan.tap_mirrors[0].source_port_name
        )
        assert 'data.openstack_networking_secgroup_v2.sandbox-internal-sg.id' in source_port
        assert plan.secgroup_name not in source_port

    def test_template_forwarding_router_port_pins_its_address(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """The router-interface port is pinned, so it cannot race the topology ports."""
        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance_forwarding, resource_prefix='stack-p1-s2'
        )

        # Asserted inside the port's own block, so it cannot pass on some other port.
        port = _resource_block(
            template_str,
            'openstack_networking_port_v2',
            'stack-p1-s2-tapm-rp-monitoring-switch',
        )
        assert (
            'subnet_id = openstack_networking_subnet_v2.stack-p1-s2-monitoring-switch-subnet.id'
        ) in port
        assert 'ip_address = "10.10.40.3"' in port

    def test_template_forwarding_user_network_ports_all_pinned(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """No port on a user network is left to Neutron, which is what makes order irrelevant."""
        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance_forwarding, resource_prefix='stack-p1-s2'
        )
        user_subnets = ('server-switch-subnet', 'monitoring-switch-subnet')

        for block in template_str.split('resource "openstack_networking_port_v2" ')[1:]:
            body = block[: block.index('\n}')]
            if any(subnet in body for subnet in user_subnets):
                assert 'ip_address = ' in body, body

    def test_template_forwarding_rejects_reserved_destination_address(
        self, mocker, open_stack_proxy, topology_instance_forwarding
    ):
        """A destination network that assigns the reserved address fails before any cloud call."""
        # The shipped definition maps the monitoring host to 10.10.40.5, so moving the
        # reservation onto offset 5 creates the clash without a new asset.
        mocker.patch.object(network_forwarding, 'ROUTER_INTERFACE_OFFSET', 5)

        with pytest.raises(CrczpException, match='10.10.40.5'):
            open_stack_proxy.validate_and_get_terraform_template(
                topology_instance_forwarding, resource_prefix='stack-p1-s2'
            )

    def test_template_forwarding_requires_hypervisor_cidr(
        self, mocker, open_stack_proxy, topology_instance_forwarding
    ):
        """Rendering fails rather than exposing the destination floating IP to everyone."""
        mocker.patch.object(open_stack_proxy, 'hypervisor_cidr', None)

        with pytest.raises(CrczpException, match='hypervisor_cidr'):
            open_stack_proxy.validate_and_get_terraform_template(
                topology_instance_forwarding, resource_prefix='stack-p1-s2'
            )

    def test_template_without_forwarding_needs_no_hypervisor_cidr(
        self, mocker, open_stack_proxy, topology_instance
    ):
        """The option is only required by topologies that actually mirror traffic."""
        mocker.patch.object(open_stack_proxy, 'hypervisor_cidr', None)

        template_str = open_stack_proxy.validate_and_get_terraform_template(topology_instance)

        assert 'openstack_networking_secgroup_rule_v2' not in template_str

    def test_template_forwarding_router_is_per_sandbox(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """Two sandboxes of one pool get distinct routers, so their subnets cannot collide."""
        templates = [
            open_stack_proxy.validate_and_get_terraform_template(
                topology_instance_forwarding, resource_prefix=prefix
            )
            for prefix in ('stack-p1-s1', 'stack-p1-s2')
        ]

        for prefix, template_str in zip(('stack-p1-s1', 'stack-p1-s2'), templates, strict=True):
            assert f'resource "openstack_networking_router_v2" "{prefix}-tapm-rtr"' in template_str
            assert (
                f'router_id = openstack_networking_router_v2.{prefix}-tapm-rtr.id'
            ) in template_str

        assert 'stack-p1-s2-tapm-rtr' not in templates[0]
        assert 'stack-p1-s1-tapm-rtr' not in templates[1]

    @pytest.mark.parametrize(
        'topology_fixture',
        ['topology_instance', 'topology_instance_volumes', 'topology_instance_forwarding'],
    )
    def test_template_blocks_are_newline_separated(
        self, request, open_stack_proxy, topology_fixture
    ):
        """Every topology renders blocks OpenTofu can parse, whatever Jinja's stripping does.

        A left-stripping comment used to weld the MAN instance's closing brace onto the
        network-forwarding data block below it, which failed every build at `tofu init`.
        """
        topology_instance = request.getfixturevalue(topology_fixture)

        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance, resource_prefix='stack-p1-s2'
        )

        _assert_blocks_newline_separated(template_str)
