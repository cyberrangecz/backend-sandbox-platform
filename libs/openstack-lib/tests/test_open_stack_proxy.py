"""Tests for crczp.openstack_driver.open_stack_proxy module."""

import json
import re

import pytest
import requests
from openstack import exceptions as sdk_exceptions
from openstack.compute.v2.flavor import Flavor
from openstack.compute.v2.keypair import Keypair
from openstack.compute.v2.limits import Limits as NovaLimits
from openstack.compute.v2.quota_set import QuotaSet as NovaQuotaSet
from openstack.compute.v2.server_remote_console import ServerRemoteConsole
from openstack.image.v2.image import Image as GlanceImage
from openstack.network.v2.quota import Quota as NeutronQuota
from openstack.network.v2.quota import QuotaDetails as NeutronQuotaDetails
from ruamel.yaml import YAML

from crczp.cloud_commons import (
    CrczpException,
    HardwareUsage,
    Image,
    InvalidTopologyDefinition,
    Limits,
    Quota,
    StackException,
    hcl_string,
)
from crczp.openstack_driver.open_stack_proxy import OpenStackProxy

PROJECT_ID = '5ef70662f8b34079a6eddb8da9d75fe8'
SERVER_ID = '9a3e6c1b-2f4d-4b8a-8e7c-5d1f0a2b3c4d'
KEYPAIR_NAME = 'crczp-p0000000012-3f2c9a4e-8b1d-4c6e-9a7f-2d5b8e1c0a93-ssh'
PUBLIC_KEY = (
    'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK0wmN/Cr3JXqmLW7u+g9pTh+wy3Kx2LjMjQ3fM0dWg5 crczp'
)
REQUEST_ID = 'req-4f1c2a3b-5d6e-4f70-8a9b-0c1d2e3f4a5b'
# What the SDK raises, without any HTTP response, for a nova older than the pinned microversion.
MICROVERSION_ERROR = (
    'A default microversion for service compute of 2.10 was requested, but the cloud only '
    'supports a minimum of 2.1 and a maximum of 2.6.'
)
CONSOLE_TYPES = ['novnc', 'xvpvnc', 'spice-html5', 'rdp-html5', 'serial', 'webmks']


def _nova_error(status: int, fault: str, message: str) -> sdk_exceptions.HttpException:
    """Build the SDK exception for a Nova error response, as the SDK parses it."""
    response = requests.Response()
    response.status_code = status
    response.headers['Content-Type'] = 'application/json'
    response.headers['x-openstack-request-id'] = REQUEST_ID
    response._content = json.dumps({fault: {'code': status, 'message': message}}).encode()  # pylint: disable=protected-access
    try:
        sdk_exceptions.raise_from_response(response)
    except sdk_exceptions.HttpException as e:
        return e
    raise AssertionError(f'HTTP {status} did not raise')


def _not_found(message: str) -> sdk_exceptions.HttpException:
    return _nova_error(404, 'itemNotFound', message)


def _conflict(message: str) -> sdk_exceptions.HttpException:
    return _nova_error(409, 'conflictingRequest', message)


def _glance_image(body: dict) -> GlanceImage:
    """Build an SDK image from a glance body, dropping its "self" link as the SDK does."""
    return GlanceImage.existing(**{key: value for key, value in body.items() if key != 'self'})


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
    def open_stack_proxy(self, fake_conn, trc):
        """Create an OpenStackProxy instance backed by a fake openstacksdk connection."""
        return OpenStackProxy(fake_conn, 'url', 'app_id', 'app_secret', trc)

    @pytest.fixture()
    def mock_flavors(self, fake_conn):
        """Make nova list the flavor every node of the test topologies uses."""
        fake_conn.compute.flavors.return_value = iter([
            Flavor.existing(id='a1-2-20', name='standard.small', vcpus=1, ram=2048, disk=20)
        ])

    @pytest.fixture()
    def expected_hardware_usage(self):
        """Return the expected HardwareUsage for the test topology."""
        return HardwareUsage(5, 10, 5, 4, 4, 12)

    def test_image_list(self, fake_conn, open_stack_proxy, glance_image, glance_image_fields):
        """Test that list_images maps glance images, including their custom properties."""
        fake_conn.image.images.return_value = iter([_glance_image(glance_image)])

        result = open_stack_proxy.list_images()

        # Image.__eq__ compares only some of the fields, so compare all of them.
        assert [vars(image) for image in result] == [glance_image_fields]
        assert vars(Image(**glance_image_fields)) == glance_image_fields
        # No page size: the SDK then follows glance's `next` links only.
        fake_conn.image.images.assert_called_once_with()

    def test_image_list_without_custom_properties(
        self, fake_conn, open_stack_proxy, glance_image_without_properties
    ):
        """Test that an image without custom properties has no default user or owner data."""
        fake_conn.image.images.return_value = iter([_glance_image(glance_image_without_properties)])

        [image] = open_stack_proxy.list_images()

        assert image.name == 'ubuntu-noble-man-0.1.0'
        assert image.os_type is None
        assert image.os_distro is None
        assert image.tags == []
        assert image.default_user is None
        assert image.owner_specified == {}

    def test_get_image(self, fake_conn, open_stack_proxy, glance_image):
        """Test that get_image fetches the image by its ID and maps it."""
        fake_conn.image.get_image.return_value = _glance_image(glance_image)

        image = open_stack_proxy.get_image(glance_image['id'])

        assert image.name == 'kali-0.6.0'
        assert image.size == 8589934592
        assert image.default_user == 'kali'
        assert image.owner_specified['owner_specified.openstack.gui_access'] == 'true'
        fake_conn.image.get_image.assert_called_once_with(glance_image['id'])

    def test_get_image_not_found(self, fake_conn, open_stack_proxy):
        """Test that a missing image is not wrapped, so the API still answers 500."""
        error = sdk_exceptions.NotFoundException('No Image found for 0000')
        fake_conn.image.get_image.side_effect = error

        with pytest.raises(sdk_exceptions.NotFoundException) as exc_info:
            open_stack_proxy.get_image('0000')

        assert exc_info.value is error

    def test_get_keypair(self, fake_conn, open_stack_proxy):
        """Test that get_keypair returns the nova keypair as a dict."""
        fake_conn.compute.get_keypair.return_value = Keypair.existing(
            name=KEYPAIR_NAME,
            public_key=PUBLIC_KEY,
            fingerprint='SHA256:0bN3v1aJqY6h2c5n0yQ5o0m3o0Zb2l4q8yM1sQ7tF6E',
            type='ssh',
            user_id='8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a',
            created_at='2026-10-01T08:15:42.000000',
            deleted=False,
            id=4242,
        )

        result = open_stack_proxy.get_keypair(KEYPAIR_NAME)

        assert result == {
            'name': KEYPAIR_NAME,
            'public_key': PUBLIC_KEY,
            'fingerprint': 'SHA256:0bN3v1aJqY6h2c5n0yQ5o0m3o0Zb2l4q8yM1sQ7tF6E',
            'type': 'ssh',
            'user_id': '8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a',
            'created_at': '2026-10-01T08:15:42.000000',
        }
        fake_conn.compute.get_keypair.assert_called_once_with(KEYPAIR_NAME)

    def test_get_keypair_nonexistent_keypair(self, fake_conn, open_stack_proxy):
        """Test that get_keypair raises CrczpException with Nova's message."""
        fake_conn.compute.get_keypair.side_effect = _not_found(
            f'Keypair {KEYPAIR_NAME} not found for user 8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a'
        )

        with pytest.raises(CrczpException) as exc_info:
            open_stack_proxy.get_keypair(KEYPAIR_NAME)

        assert str(exc_info.value) == (
            f"Failed to get keypair '{KEYPAIR_NAME}': Keypair {KEYPAIR_NAME} not found for user "
            f'8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a (HTTP 404) (Request-ID: {REQUEST_ID})'
        )

    def test_get_keypair_sdk_error(self, fake_conn, open_stack_proxy):
        """Test that an SDK error without an HTTP response is reported by its message."""
        fake_conn.compute.get_keypair.side_effect = sdk_exceptions.ConfigException(
            MICROVERSION_ERROR
        )

        with pytest.raises(CrczpException) as exc_info:
            open_stack_proxy.get_keypair(KEYPAIR_NAME)

        assert (
            str(exc_info.value) == f"Failed to get keypair '{KEYPAIR_NAME}': {MICROVERSION_ERROR}"
        )

    def test_create_keypair(self, fake_conn, open_stack_proxy):
        """Test that create_keypair sends the name, public key and key type."""
        fake_conn.compute.create_keypair.return_value = Keypair.existing(name=KEYPAIR_NAME)

        result = open_stack_proxy.create_keypair(KEYPAIR_NAME, PUBLIC_KEY, 'ssh')

        assert result is None
        fake_conn.compute.create_keypair.assert_called_once_with(
            name=KEYPAIR_NAME, public_key=PUBLIC_KEY, type='ssh'
        )

    @pytest.mark.parametrize('public_key', [None, ''])
    def test_create_keypair_without_public_key(self, fake_conn, open_stack_proxy, public_key):
        """Test that a missing public key is left out, so that nova generates the key."""
        fake_conn.compute.create_keypair.return_value = Keypair.existing(name=KEYPAIR_NAME)

        open_stack_proxy.create_keypair(KEYPAIR_NAME, public_key, 'x509')

        fake_conn.compute.create_keypair.assert_called_once_with(name=KEYPAIR_NAME, type='x509')

    def test_create_keypair_already_exist(self, fake_conn, open_stack_proxy):
        """Test that create_keypair raises CrczpException when the keypair already exists."""
        fake_conn.compute.create_keypair.side_effect = _conflict(
            f"Key pair '{KEYPAIR_NAME}' already exists."
        )

        with pytest.raises(CrczpException, match=r'\(HTTP 409\)'):
            open_stack_proxy.create_keypair(KEYPAIR_NAME, PUBLIC_KEY, 'ssh')

    def test_delete_keypair(self, fake_conn, open_stack_proxy):
        """Test that delete_keypair asks nova to report a missing keypair."""
        open_stack_proxy.delete_keypair(KEYPAIR_NAME)

        fake_conn.compute.delete_keypair.assert_called_once_with(KEYPAIR_NAME, ignore_missing=False)

    def test_delete_keypair_nonexistent_keypair(self, fake_conn, open_stack_proxy):
        """Test that delete_keypair raises CrczpException when the keypair does not exist."""
        fake_conn.compute.delete_keypair.side_effect = _not_found(
            f'Keypair {KEYPAIR_NAME} not found for user 8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a'
        )

        with pytest.raises(CrczpException, match=r'\(HTTP 404\)'):
            open_stack_proxy.delete_keypair(KEYPAIR_NAME)

    def test_instance_reboot(self, fake_conn, open_stack_proxy):
        """Test that reboot_instance soft-reboots the server."""
        open_stack_proxy.reboot_instance(SERVER_ID)

        fake_conn.compute.reboot_server.assert_called_once_with(SERVER_ID, 'SOFT')

    def test_instance_reboot_stack_or_instance_not_found(self, fake_conn, open_stack_proxy):
        """Test that reboot_instance raises CrczpException when the instance is not found."""
        fake_conn.compute.reboot_server.side_effect = _not_found(
            f'Instance {SERVER_ID} could not be found.'
        )

        with pytest.raises(CrczpException):
            open_stack_proxy.reboot_instance(SERVER_ID)

    def test_instance_start(self, fake_conn, open_stack_proxy):
        """Test that start_instance starts the server."""
        open_stack_proxy.start_instance(SERVER_ID)

        fake_conn.compute.start_server.assert_called_once_with(SERVER_ID)

    def test_instance_start_stack_or_instance_not_found(self, fake_conn, open_stack_proxy):
        """Test that start_instance raises CrczpException when the instance is not found."""
        fake_conn.compute.start_server.side_effect = _not_found(
            f'Instance {SERVER_ID} could not be found.'
        )

        with pytest.raises(CrczpException):
            open_stack_proxy.start_instance(SERVER_ID)

    def test_instance_resume(self, fake_conn, open_stack_proxy):
        """Test that resume_instance resumes the server."""
        open_stack_proxy.resume_instance(SERVER_ID)

        fake_conn.compute.resume_server.assert_called_once_with(SERVER_ID)

    def test_instance_resume_stack_or_instance_not_found(self, fake_conn, open_stack_proxy):
        """Test that resume_instance raises CrczpException when the instance is not found."""
        fake_conn.compute.resume_server.side_effect = _not_found(
            f'Instance {SERVER_ID} could not be found.'
        )

        with pytest.raises(CrczpException):
            open_stack_proxy.resume_instance(SERVER_ID)

    def test_instance_resume_conflict_message(self, fake_conn, open_stack_proxy):
        """Test that the error carries Nova's message, status and request ID, but no URL."""
        fake_conn.compute.resume_server.side_effect = _conflict(
            f"Cannot 'resume' instance {SERVER_ID} while it is in vm_state active"
        )

        with pytest.raises(CrczpException) as exc_info:
            open_stack_proxy.resume_instance(SERVER_ID)

        assert str(exc_info.value) == (
            f"Failed to resume instance with id={SERVER_ID}: Cannot 'resume' instance "
            f'{SERVER_ID} while it is in vm_state active (HTTP 409) (Request-ID: {REQUEST_ID}).'
        )

    @pytest.mark.parametrize('console_type', CONSOLE_TYPES)
    def test_get_console_url(self, fake_conn, open_stack_proxy, console_type):
        """Test that get_console_url creates a remote console of the requested type."""
        expected_url = f'https://console.cloud.test:6082/{console_type}?token=4c1f2a3b'
        fake_conn.compute.create_server_remote_console.return_value = ServerRemoteConsole.existing(
            type=console_type, url=expected_url
        )

        result = open_stack_proxy.get_console_url(SERVER_ID, console_type)

        assert result == expected_url
        fake_conn.compute.create_server_remote_console.assert_called_once_with(
            SERVER_ID, type=console_type
        )

    def test_get_console_url_unsupported_type(self, fake_conn, open_stack_proxy):
        """Test that an unknown console type is rejected before any request."""
        with pytest.raises(StackException) as exc_info:
            open_stack_proxy.get_console_url(SERVER_ID, 'foo')

        assert str(exc_info.value) == (
            'OpenStack error while getting console: Unsupported console_type "foo"'
        )
        fake_conn.compute.create_server_remote_console.assert_not_called()

    def test_get_console_url_instance_or_stack_not_found(self, fake_conn, open_stack_proxy):
        """Test that a console error carries only Nova's message."""
        fake_conn.compute.create_server_remote_console.side_effect = _not_found(
            f'Instance {SERVER_ID} could not be found.'
        )

        with pytest.raises(StackException) as exc_info:
            open_stack_proxy.get_console_url(SERVER_ID, 'spice-html5')

        assert str(exc_info.value) == (
            f'OpenStack error while getting console: Instance {SERVER_ID} could not be found.'
        )

    def test_get_console_url_value_error(self, fake_conn, open_stack_proxy):
        """Test that the SDK's ValueError for an unsupported console becomes StackException."""
        fake_conn.compute.create_server_remote_console.side_effect = ValueError(
            'Console type webmks is not supported on server side'
        )

        with pytest.raises(StackException) as exc_info:
            open_stack_proxy.get_console_url(SERVER_ID, 'webmks')

        assert str(exc_info.value) == (
            'OpenStack error while getting console: '
            'Console type webmks is not supported on server side'
        )

    def test_get_console_url_sdk_error(self, fake_conn, open_stack_proxy):
        """Test that an SDK error without an HTTP response becomes StackException."""
        fake_conn.compute.create_server_remote_console.side_effect = sdk_exceptions.ConfigException(
            MICROVERSION_ERROR
        )

        with pytest.raises(StackException) as exc_info:
            open_stack_proxy.get_console_url(SERVER_ID, 'novnc')

        assert str(exc_info.value) == f'OpenStack error while getting console: {MICROVERSION_ERROR}'

    def test_get_quota_set(self, fake_conn, open_stack_proxy):
        """Test that get_quota_set returns a correctly populated QuotaSet.

        Unlimited quotas (-1) pass through unchanged, and so become -0.0 GB of RAM.
        """
        # QuotaSet.existing() does not split a detail body into limits and usage; the parsing
        # of a real body is covered in test_sdk_contract.py.
        fake_conn.compute.get_quota_set.return_value = NovaQuotaSet(
            cores=-1, ram=-1, instances=50, usage={'cores': 8, 'ram': 16384, 'instances': 4}
        )
        fake_conn.network.get_quota.return_value = NeutronQuotaDetails.existing(
            network={'limit': 100, 'used': 2, 'reserved': 0},
            subnet={'limit': 120, 'used': 3, 'reserved': 0},
            port={'limit': -1, 'used': 9, 'reserved': 0},
        )

        result = open_stack_proxy.get_quota_set(PROJECT_ID)

        assert vars(result) == {
            'vcpu': Quota(-1, 8),
            'ram': Quota(-0.0, 16.4),
            'instances': Quota(50, 4),
            'network': Quota(100, 2),
            'subnet': Quota(120, 3),
            'port': Quota(-1, 9),
        }
        assert str(result.ram.limit) == '-0.0'
        fake_conn.compute.get_quota_set.assert_called_once_with(PROJECT_ID, usage=True)
        fake_conn.network.get_quota.assert_called_once_with(PROJECT_ID, details=True)

    def test_get_flavors_dict(self):
        """Test that _get_flavors_dict maps flavor objects to the expected dict structure."""
        flavors = [
            Flavor.existing(id='a1-2-20', name='a1-2-20', vcpus=1, ram=2048, disk=20),
            Flavor.existing(id='a2-4-35', name='standard.medium', vcpus=2, ram=4096, disk=35),
        ]

        result = OpenStackProxy._get_flavors_dict(flavors)  # pylint: disable=protected-access

        assert result == {
            'a1-2-20': {'vcpu': 1, 'ram': 2.0},
            'standard.medium': {'vcpu': 2, 'ram': 4.1},
        }

    def test_get_flavors_dict_lists_public_flavors(self, fake_conn, open_stack_proxy):
        """Test that get_flavors_dict lists the public flavors and converts RAM to GB."""
        fake_conn.compute.flavors.return_value = iter([
            Flavor.existing(id='a4-16-80', name='a4-16-80', vcpus=4, ram=16384, disk=80)
        ])

        result = open_stack_proxy.get_flavors_dict()

        assert result == {'a4-16-80': {'vcpu': 4, 'ram': 16.4}}
        fake_conn.compute.flavors.assert_called_once_with(is_public=True)

    @pytest.mark.usefixtures('mock_flavors')
    def test_get_hardware_usage(self, open_stack_proxy, topology_instance, expected_hardware_usage):
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

    def test_get_project_limits(self, fake_conn, open_stack_proxy):
        """Test that get_project_limits returns correct Limits from nova and neutron."""
        fake_conn.compute.get_limits.return_value = NovaLimits.existing(
            absolute={
                'maxTotalCores': 100,
                'maxTotalRAMSize': 102400,
                'maxTotalInstances': 50,
                'totalCoresUsed': 20,
                'totalRAMUsed': 65536,
                'totalInstancesUsed': 14,
            }
        )
        fake_conn.network.get_quota.return_value = NeutronQuota.existing(
            network=100, subnet=120, port=400, router=10
        )

        result = open_stack_proxy.get_project_limits(PROJECT_ID)

        assert result == Limits(
            vcpu=100, ram=102.4, instances=50, network=100, subnet=120, port=400
        )
        fake_conn.compute.get_limits.assert_called_once_with()
        fake_conn.network.get_quota.assert_called_once_with(PROJECT_ID)

    @pytest.mark.parametrize(
        ('missing_key', 'field'),
        [
            ('maxTotalCores', 'vcpu'),
            ('maxTotalRAMSize', 'ram'),
            ('maxTotalInstances', 'instances'),
        ],
    )
    def test_get_project_limits_missing_nova_key(
        self, fake_conn, open_stack_proxy, missing_key, field
    ):
        """Test that a limit missing from nova's answer fails on that limit's field."""
        absolute = {'maxTotalCores': 100, 'maxTotalRAMSize': 102400, 'maxTotalInstances': 100}
        del absolute[missing_key]
        fake_conn.compute.get_limits.return_value = NovaLimits.existing(absolute=absolute)
        fake_conn.network.get_quota.return_value = NeutronQuota.existing(
            network=100, subnet=100, port=400
        )

        with pytest.raises(TypeError, match=f"missing 1 required positional argument: '{field}'"):
            open_stack_proxy.get_project_limits(PROJECT_ID)

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
            'name = "stack-p1-s2-tapm-rtr" tags = ["stack-p1-s2"] admin_state_up = "true" '
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
            'pool = data.openstack_networking_network_v2.network-forwarding-ext-net.name '
            'tags = ["stack-p1-s2"] }'
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
