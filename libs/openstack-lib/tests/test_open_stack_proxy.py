"""Tests for crczp.openstack_driver.open_stack_proxy module."""

import re

import pytest
from novaclient.exceptions import ClientException as NovaClientException
from ruamel.yaml import YAML

from crczp.cloud_commons import (
    CrczpException,
    HardwareUsage,
    Image,
    InvalidTopologyDefinition,
    Limits,
    Quota,
    QuotaSet,
    StackException,
    hcl_string,
)
from crczp.openstack_driver import utils
from crczp.openstack_driver.open_stack_proxy import OpenStackProxy


def _resource_block(template: str, resource_type: str, name: str) -> str:
    """Return the body of a single Terraform resource block."""
    start = template.index(f'resource "{resource_type}" "{name}" {{')
    return template[start : template.index('\n}', start)]


def _squash(text: str) -> str:
    """Collapse whitespace runs, so assertions do not depend on column alignment."""
    return re.sub(r'\s+', ' ', text)


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

    @pytest.mark.usefixtures('mock_flavors')
    def test_get_hardware_usage_counts_forwarding_router_port(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """The forwarding router's interface on the destination network uses a port too."""
        result = open_stack_proxy.get_hardware_usage(topology_instance_forwarding)

        assert len(list(topology_instance_forwarding.get_links())) == 11
        assert result.port == 12

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
        assert 'sandbox-mirror-sg' not in template_str

    def test_template_volumes_render_per_volume_images(
        self, open_stack_proxy, topology_instance_volumes
    ):
        """The system disk comes from base_box.image; later volumes from their own image or blank.

        The 'server' host declares a system disk, an extra volume with its own image, and a blank
        extra volume.
        """
        server_node = topology_instance_volumes.get_node('server')
        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance_volumes
        )
        squashed = _squash(template_str)
        server = _squash(
            _resource_block(template_str, 'openstack_compute_instance_v2', 'stack-name-server')
        )

        assert sorted(set(re.findall(r'image_data_source-[\w-]+', template_str))) == [
            'image_data_source-stack-name-server-0',
            'image_data_source-stack-name-server-1',
        ]
        assert (
            'data "openstack_images_image_ids_v2" "image_data_source-stack-name-server-0" { '
            f'name = "{server_node.base_box.image}" }}' in squashed
        )
        assert (
            'data "openstack_images_image_ids_v2" "image_data_source-stack-name-server-1" { '
            'name = "data-disk-x86_64" }' in squashed
        )

        image_ids = 'data.openstack_images_image_ids_v2.image_data_source-stack-name-server'
        assert 'image_name' not in server
        assert server.count('block_device {') == 3
        assert (
            'block_device { '
            f'uuid = {image_ids}-0.ids[0] '
            'source_type = "image" destination_type = "volume" boot_index = 0 volume_size = 20 '
            'delete_on_termination = true }' in server
        )
        assert (
            'block_device { '
            f'uuid = {image_ids}-1.ids[0] '
            'source_type = "image" destination_type = "volume" boot_index = -1 volume_size = 30 '
            'delete_on_termination = true }' in server
        )
        assert (
            'block_device { source_type = "blank" destination_type = "volume" boot_index = -1 '
            'volume_size = 40 delete_on_termination = true }' in server
        )

        home = _resource_block(template_str, 'openstack_compute_instance_v2', 'stack-name-home')
        assert 'image_name = "debian-12-x86_64"' in home
        assert 'block_device' not in home

    def test_template_volume_image_cannot_inject_terraform(
        self, open_stack_proxy, topology_instance_volumes
    ):
        """A hostile volume image name stays inside its string literal.

        Image names are free text from the topology definition. Left unescaped, a quote closes
        the string, a brace closes the block, and `${...}` would be evaluated by OpenTofu.
        """
        payload = (
            '"\n}\nresource "openstack_networking_secgroup_rule_v2" "inj" {}\n'
            '${file("/etc/hostname")}'
        )
        server = topology_instance_volumes.get_node('server')
        server.volumes[1].image = payload

        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance_volumes
        )

        data_source = re.search(
            r'data "openstack_images_image_ids_v2" "image_data_source-stack-name-server-1" \{\n'
            r'  name = (.*)\n\}',
            template_str,
        )
        assert data_source is not None
        assert data_source.group(1) == hcl_string(payload)
        assert not re.findall(
            r'^resource "openstack_networking_secgroup_rule_v2"', template_str, re.MULTILINE
        )
        assert '$${file(' in template_str
        assert not re.findall(r'(?<!\$)\$\{', template_str)
        _assert_blocks_newline_separated(template_str)

    def test_template_image_and_flavor_cannot_inject_terraform(
        self, open_stack_proxy, topology_instance_volumes
    ):
        """The image and flavor of a host without volumes are escaped as well."""
        payload = '"\n}\nresource "openstack_networking_secgroup_rule_v2" "inj" {}\n${var.x}'
        home = topology_instance_volumes.get_node('home')
        home.base_box.image = payload
        home.flavor = payload

        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance_volumes
        )

        home_block = _resource_block(
            template_str, 'openstack_compute_instance_v2', 'stack-name-home'
        )
        assert f'image_name = {hcl_string(payload)}\n' in home_block
        assert f'flavor_name = {hcl_string(payload)}\n' in home_block
        assert not re.findall(
            r'^resource "openstack_networking_secgroup_rule_v2"', template_str, re.MULTILINE
        )
        assert not re.findall(r'(?<!\$)\$\{', template_str)

    def test_template_forwarding_emits_tap_mirror(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """A definition with network_forwarding emits FIP plumbing and a tap_mirror."""
        template_str = _squash(
            open_stack_proxy.validate_and_get_terraform_template(
                topology_instance_forwarding, resource_prefix='stack-p1-s2'
            )
        )

        # The external network is the one of the router the platform base deployment creates.
        assert (
            'data "openstack_networking_router_v2" "network-forwarding-platform-router" '
            '{ name = "base-router-public" }'
        ) in template_str
        assert (
            'data "openstack_networking_network_v2" "network-forwarding-ext-net" { network_id = '
            'data.openstack_networking_router_v2.network-forwarding-platform-router'
            '.external_network_id }'
        ) in template_str
        assert 'data "openstack_networking_port_v2"' not in template_str

        # The sandbox gets its own router, gatewayed to the platform's external network.
        assert (
            'resource "openstack_networking_router_v2" "stack-p1-s2-tapm-rtr" { '
            'name = "stack-p1-s2-tapm-rtr" admin_state_up = "true" '
            'external_network_id = '
            'data.openstack_networking_network_v2.network-forwarding-ext-net.id }'
        ) in template_str
        assert (
            'resource "openstack_networking_router_interface_v2" '
            '"stack-p1-s2-tapm-ri-monitoring-switch" { '
            'router_id = openstack_networking_router_v2.stack-p1-s2-tapm-rtr.id '
            'port_id = openstack_networking_port_v2.stack-p1-s2-tapm-rp-monitoring-switch.id }'
        ) in template_str

        # The floating IP of the destination port (link-7, monitoring on monitoring-switch).
        assert (
            'resource "openstack_networking_floatingip_v2" "stack-p1-s2-tapm-fip-link-7" { '
            'pool = data.openstack_networking_network_v2.network-forwarding-ext-net.name }'
        ) in template_str
        assert (
            'resource "openstack_networking_floatingip_associate_v2" '
            '"stack-p1-s2-tapm-fipa-link-7" { '
            'floating_ip = openstack_networking_floatingip_v2.stack-p1-s2-tapm-fip-link-7.address '
            'port_id = openstack_networking_port_v2.stack-p1-s2-link-7.id '
            'depends_on = [openstack_networking_router_interface_v2.'
            'stack-p1-s2-tapm-ri-monitoring-switch] }'
        ) in template_str

        # The source port (link-5, server on server-switch) is mirrored into that floating IP;
        # sandbox id 2 -> tunnel base 2 * 1024 = 2048, direction "both" -> in/out.
        assert (
            'resource "openstack_taas_tap_mirror_v2" "stack-p1-s2-tapm-0" { '
            'name = "stack-p1-s2-tapm-0" mirror_type = "gre" '
            'port_id = openstack_networking_port_v2.stack-p1-s2-link-5.id '
            'remote_ip = openstack_networking_floatingip_v2.stack-p1-s2-tapm-fip-link-7.address '
            'directions { in = 2048 out = 2049 } }'
        ) in template_str

    def test_template_forwarding_validation_render_starts_tunnel_ids_at_zero(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """The import-time validation render has no resource prefix and so no sandbox id."""
        template_str = _squash(
            open_stack_proxy.validate_and_get_terraform_template(topology_instance_forwarding)
        )

        assert 'directions { in = 0 out = 1 }' in template_str

    def test_template_forwarding_rejects_stack_name_without_sandbox_id(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """Tunnel ids are never silently shared because of an unparseable stack name."""
        with pytest.raises(CrczpException, match='"my-stack"'):
            open_stack_proxy.validate_and_get_terraform_template(
                topology_instance_forwarding, resource_prefix='my-stack'
            )

    def test_template_without_forwarding_ignores_stack_name(
        self, open_stack_proxy, topology_instance
    ):
        """Only a forwarding topology needs a sandbox id in the stack name."""
        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance, resource_prefix='my-stack'
        )

        assert 'openstack_taas_tap_mirror_v2' not in template_str

    def test_template_forwarding_destination_network_ports_use_mirror_secgroup(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """Every port on the destination network swaps the topology group for the mirror group.

        The mirror group is created by the platform base deployment, so the sandbox looks it up
        as a data source and creates no security group or rule of its own.
        """
        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance_forwarding, resource_prefix='stack-p1-s2'
        )
        mirror_sg = 'data.openstack_networking_secgroup_v2.sandbox-mirror-sg.id'
        destination_subnet = (
            'subnet_id = openstack_networking_subnet_v2.stack-p1-s2-monitoring-switch-subnet.id'
        )
        # monitoring and server-router on monitoring-switch.
        destination_ports = {'stack-p1-s2-link-7', 'stack-p1-s2-link-11'}

        assert 'data "openstack_networking_secgroup_v2" "sandbox-mirror-sg" {' in template_str
        assert 'resource "openstack_networking_secgroup_v2"' not in template_str
        assert 'resource "openstack_networking_secgroup_rule_v2"' not in template_str

        # Security group rules are a union, so these ports must not keep the topology group
        # as well — that one admits 0.0.0.0/0.
        port_names = re.findall(r'resource "openstack_networking_port_v2" "([^"]+)"', template_str)
        assert destination_ports <= set(port_names)
        for port_name in port_names:
            port = _resource_block(template_str, 'openstack_networking_port_v2', port_name)
            if port_name in destination_ports:
                assert destination_subnet in port, port_name
                assert mirror_sg in port, port_name
                assert 'sandbox-internal-sg' not in port, port_name
            else:
                assert 'sandbox-mirror-sg' not in port, port_name

        source_port = _resource_block(
            template_str, 'openstack_networking_port_v2', 'stack-p1-s2-link-5'
        )
        assert 'data.openstack_networking_secgroup_v2.sandbox-internal-sg.id' in source_port

    def test_template_forwarding_router_port_is_addressed_last(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """Neutron addresses the router port after every topology port on the destination network.

        So it cannot take an address one of them asks for.
        """
        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance_forwarding, resource_prefix='stack-p1-s2'
        )

        port = _squash(
            _resource_block(
                template_str,
                'openstack_networking_port_v2',
                'stack-p1-s2-tapm-rp-monitoring-switch',
            )
        )
        assert (
            'fixed_ip { subnet_id = '
            'openstack_networking_subnet_v2.stack-p1-s2-monitoring-switch-subnet.id }'
        ) in port
        assert 'ip_address' not in port
        assert (
            'depends_on = [ openstack_networking_port_v2.stack-p1-s2-link-7, '
            'openstack_networking_port_v2.stack-p1-s2-link-11, ]'
        ) in port

    def test_template_forwarding_rejects_other_host_on_destination_network(
        self, open_stack_proxy, build_forwarding_instance, server_on_monitoring_switch
    ):
        """A host sharing the destination network fails before any cloud call."""
        instance = build_forwarding_instance(extra_net_mappings=server_on_monitoring_switch)

        with pytest.raises(InvalidTopologyDefinition, match='"server" is mapped to it'):
            open_stack_proxy.validate_and_get_terraform_template(
                instance, resource_prefix='stack-p1-s2'
            )

    def test_template_forwarding_accepts_unmanaged_destination(
        self, open_stack_proxy, build_forwarding_instance
    ):
        """An unmanaged host, the appliance use case, can be the mirror destination."""
        instance = build_forwarding_instance(unmanaged_host='monitoring')

        template_str = open_stack_proxy.validate_and_get_terraform_template(
            instance, resource_prefix='stack-p1-s2'
        )

        assert 'openstack_taas_tap_mirror_v2' in template_str

    def test_template_forwarding_rejects_validation_prefix_as_stack_name(
        self, open_stack_proxy, topology_instance_forwarding
    ):
        """The placeholder name used for validation is not a stack name that gets tunnel ids."""
        with pytest.raises(CrczpException, match='"stack-name"'):
            open_stack_proxy.validate_and_get_terraform_template(
                topology_instance_forwarding, resource_prefix='stack-name'
            )

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
        """Every topology renders newline-separated blocks that OpenTofu can parse."""
        topology_instance = request.getfixturevalue(topology_fixture)

        template_str = open_stack_proxy.validate_and_get_terraform_template(
            topology_instance, resource_prefix='stack-p1-s2'
        )

        _assert_blocks_newline_separated(template_str)
