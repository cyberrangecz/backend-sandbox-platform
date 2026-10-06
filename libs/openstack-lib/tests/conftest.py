"""Shared pytest fixtures for the OpenStack driver test suite."""

import io
import os

import pytest
import yaml

from crczp.cloud_commons import TopologyInstance, TransformationConfiguration
from crczp.openstack_driver import CrczpOpenStackClient
from crczp.topology_definition.models import TopologyDefinition

TESTING_DATA_DIR = 'assets'

TESTING_DEFINITION = 'definition.yml'
TESTING_DEFINITION_EMPTY = 'definition-empty.yml'
TESTING_DEFINITION_FORWARDING = 'definition-forwarding.yml'
TESTING_TRANSFORMATION_CONFIGURATION = 'trc-config.yml'
TESTING_GENERATED_HEAT_TEMPLATE = 'generated-template.tf'
TESTING_BASE_NETWORK_TEMPLATE = 'base-net-template.yml'

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
def empty_topology_definition():
    """Create an empty topology definition."""
    with open(data_path_join(TESTING_DEFINITION_EMPTY), encoding='utf-8') as file:
        return TopologyDefinition.load(file)


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


@pytest.fixture
def base_network_template():
    """Load the base network template as a string."""
    with open(data_path_join(TESTING_BASE_NETWORK_TEMPLATE), encoding='utf-8') as file:
        return file.read()


@pytest.fixture()
def os_client(mocker, trc):  # pylint: disable=redefined-outer-name
    """Create a mocked CrczpOpenStackClient instance."""
    kwargs = {
        'auth_url': mocker.MagicMock(),
        'application_credential_id': mocker.MagicMock(),
        'application_credential_secret': mocker.MagicMock(),
        'trc': trc,
    }

    return CrczpOpenStackClient(**kwargs)
