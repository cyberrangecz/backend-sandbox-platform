"""Shared pytest fixtures for the OpenStack driver test suite."""

import io
import os

import pytest
import yaml
from openstack import connection
from openstack.compute import compute_service
from openstack.image import image_service
from openstack.network import network_service
from openstack.test import fakes

from crczp.cloud_commons import TopologyInstance, TransformationConfiguration
from crczp.openstack_driver import CrczpOpenStackClient, utils
from crczp.topology_definition.models import TopologyDefinition

TESTING_DATA_DIR = 'assets'

TESTING_DEFINITION = 'definition.yml'
TESTING_DEFINITION_FORWARDING = 'definition-forwarding.yml'
TESTING_TRANSFORMATION_CONFIGURATION = 'trc-config.yml'
TESTING_GENERATED_HEAT_TEMPLATE = 'generated-template.tf'

_SERVER_ON_MONITORING_SWITCH = """\
  - host: server
    network: monitoring-switch
    ip: 10.10.40.6

"""

SERVER_VOLUMES = """\
    volumes:
      - size: 20
      - size: 30
        image: data-disk-x86_64
      - size: 40
"""


def data_path_join(file: str, data_dir: str = TESTING_DATA_DIR) -> str:
    """Return the absolute path to a test data file."""
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), data_dir, file)


@pytest.fixture
def topology_definition():
    """Create an example topology definition."""
    with open(data_path_join(TESTING_DEFINITION), encoding='utf-8') as file:
        return TopologyDefinition.load(file)


@pytest.fixture
def topology_instance(topology_definition, trc):  # pylint: disable=redefined-outer-name
    """Create a TopologyInstance from the example definition and TRC."""
    return TopologyInstance(topology_definition, trc)


@pytest.fixture
def forwarding_definition():
    """Create a topology definition that uses network forwarding."""
    with open(data_path_join(TESTING_DEFINITION_FORWARDING), encoding='utf-8') as file:
        return TopologyDefinition.load(file)


@pytest.fixture
def topology_instance_forwarding(forwarding_definition, trc):  # pylint: disable=redefined-outer-name
    """Create a TopologyInstance from a definition that uses network forwarding."""
    return TopologyInstance(forwarding_definition, trc)


@pytest.fixture
def server_on_monitoring_switch() -> str:
    """Return a mapping that puts a second host on the monitoring switch."""
    return _SERVER_ON_MONITORING_SWITCH


@pytest.fixture
def build_forwarding_instance(trc):  # pylint: disable=redefined-outer-name
    """Return a factory for the forwarding topology with extra YAML spliced into its text.

    The edits are applied before loading, so the topology schema validators still run on them.
    ``extra_net_mappings`` lands after the existing net_mappings, ``extra_sections`` after the
    last top-level section, and ``unmanaged_host`` gets ``managed: false``.
    """

    def build(
        extra_net_mappings: str = '', extra_sections: str = '', unmanaged_host: str = ''
    ) -> TopologyInstance:
        with open(data_path_join(TESTING_DEFINITION_FORWARDING), encoding='utf-8') as file:
            text = file.read()
        if unmanaged_host:
            host_line = f'  - name: {unmanaged_host}\n'
            text = text.replace(host_line, host_line + '    managed: false\n', 1)
        text = text.replace('router_mappings:', extra_net_mappings + 'router_mappings:', 1)
        definition = TopologyDefinition.load(io.StringIO(text + extra_sections))
        return TopologyInstance(definition, trc)

    return build


@pytest.fixture
def topology_instance_volumes(trc):  # pylint: disable=redefined-outer-name
    """Create a TopologyInstance whose 'server' host declares extra volumes."""
    with open(data_path_join(TESTING_DEFINITION), encoding='utf-8') as file:
        text = file.read()
    server_end = '    hidden: True\n'
    text = text.replace(server_end, server_end + SERVER_VOLUMES, 1)
    return TopologyInstance(TopologyDefinition.load(io.StringIO(text)), trc)


@pytest.fixture
def trc():
    """Create a transformation configuration."""
    path = data_path_join(TESTING_TRANSFORMATION_CONFIGURATION)
    return TransformationConfiguration.from_file(path)


@pytest.fixture
def generated_terraform_template():
    """Create a generated Terraform template."""
    with open(data_path_join(TESTING_GENERATED_HEAT_TEMPLATE), encoding='utf-8') as file:
        return yaml.safe_load(file)


@pytest.fixture(scope='session')
def fake_proxies():
    """Create autospecced compute, image (v2) and network proxies once per session.

    Generating them inspects every proxy method, which is too slow to repeat per test.
    """
    return (
        fakes.generate_fake_proxy(compute_service.ComputeService),
        fakes.generate_fake_proxy(image_service.ImageService, '2'),
        fakes.generate_fake_proxy(network_service.NetworkService),
    )


@pytest.fixture
def fake_conn(mocker, fake_proxies):  # pylint: disable=redefined-outer-name
    """Create a fake openstacksdk Connection with autospecced service proxies.

    The proxies reject unknown method names and arguments that do not fit the signature,
    but not unknown keyword arguments of methods that take **kwargs (the wire-level checks
    are in test_sdk_contract.py). Every method returns a MagicMock until a test sets its
    return_value or side_effect.
    """
    conn = mocker.create_autospec(connection.Connection, instance=True)
    for proxy in fake_proxies:
        # Drops the previous test's calls and return values, and the fake resources
        # generate_fake_proxy presets as side_effect, which would override return_value.
        proxy.reset_mock(return_value=True, side_effect=True)
    conn.compute, conn.image, conn.network = fake_proxies
    return conn


@pytest.fixture
def glance_image():
    """Return an image as the glance v2 API sends it, with custom properties at the top level."""
    return {
        'id': '7c1d4e5a-3b6f-4e2a-9d8c-1f0a2b3c4d5e',
        'name': 'kali-0.6.0',
        'status': 'active',
        'visibility': 'public',
        'os_hidden': False,
        'protected': False,
        'owner': '5ef70662f8b34079a6eddb8da9d75fe8',
        'checksum': '8f6d1b0e5c1c4a1e9a2f3b4c5d6e7f80',
        'os_hash_algo': 'sha512',
        'os_hash_value': 'a3f1c0b7' * 16,
        'min_disk': 25,
        'min_ram': 2048,
        'size': 8589934592,
        'virtual_size': 26843545600,
        'disk_format': 'qcow2',
        'container_format': 'bare',
        'created_at': '2025-03-12T09:41:27Z',
        'updated_at': '2025-03-12T09:47:03Z',
        'tags': ['crczp', 'gui'],
        'os_distro': 'debian',
        'os_type': 'linux',
        'hw_qemu_guest_agent': 'yes',
        'default_user': 'kali',
        'owner_specified.openstack.gui_access': 'true',
        'owner_specified.openstack.custom': 'true',
        'owner_specified.openstack.version': '0.6.0',
        'owner_specified.openstack.md5': '8f6d1b0e5c1c4a1e9a2f3b4c5d6e7f80',
        'owner_specified.openstack.sha256': 'c0ffee00' * 8,
        'owner_specified.openstack.object': 'images/kali-0.6.0',
        'stores': 'ceph',
        'self': '/v2/images/7c1d4e5a-3b6f-4e2a-9d8c-1f0a2b3c4d5e',
        'file': '/v2/images/7c1d4e5a-3b6f-4e2a-9d8c-1f0a2b3c4d5e/file',
        'schema': '/v2/schemas/image',
    }


@pytest.fixture
def glance_image_fields(glance_image):  # pylint: disable=redefined-outer-name
    """Return the attributes the driver's Image must have for glance_image."""
    return {
        'os_distro': 'debian',
        'os_type': 'linux',
        'disk_format': 'qcow2',
        'container_format': 'bare',
        'visibility': 'public',
        'size': 8589934592,
        'status': 'active',
        'min_ram': 2048,
        'min_disk': 25,
        'created_at': '2025-03-12T09:41:27Z',
        'updated_at': '2025-03-12T09:47:03Z',
        'tags': ['crczp', 'gui'],
        'default_user': 'kali',
        'name': 'kali-0.6.0',
        'owner_specified': {
            key: value for key, value in glance_image.items() if key.startswith('owner_specified.')
        },
    }


@pytest.fixture
def glance_image_without_properties():
    """Return an image from the glance v2 API without custom properties, os_type or tags."""
    return {
        'id': '0b9e8d7c-6a5f-4e3d-8c2b-1a0f9e8d7c6b',
        'name': 'ubuntu-noble-man-0.1.0',
        'status': 'active',
        'visibility': 'public',
        'os_hidden': False,
        'protected': False,
        'owner': '5ef70662f8b34079a6eddb8da9d75fe8',
        'min_disk': 0,
        'min_ram': 0,
        'size': 629145600,
        'virtual_size': None,
        'disk_format': 'raw',
        'container_format': 'bare',
        'created_at': '2026-02-03T14:05:51Z',
        'updated_at': '2026-02-03T14:06:10Z',
        'tags': [],
        'self': '/v2/images/0b9e8d7c-6a5f-4e3d-8c2b-1a0f9e8d7c6b',
        'file': '/v2/images/0b9e8d7c-6a5f-4e3d-8c2b-1a0f9e8d7c6b/file',
        'schema': '/v2/schemas/image',
    }


@pytest.fixture()
def os_client(mocker, trc, fake_conn):  # pylint: disable=redefined-outer-name
    """Create a CrczpOpenStackClient whose openstacksdk connection is fake_conn."""
    mocker.patch.object(utils, 'get_connection', return_value=fake_conn)
    kwargs = {
        'auth_url': 'http://keystone.test:5000/v3',
        'application_credential_id': 'app-cred-id',
        'application_credential_secret': 'app-cred-secret',
        'trc': trc,
    }

    return CrczpOpenStackClient(**kwargs)
