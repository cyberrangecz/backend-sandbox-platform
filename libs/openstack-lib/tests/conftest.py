import os

import pytest
import yaml
from crczp.cloud_commons import TransformationConfiguration, TopologyInstance
from crczp.topology_definition.models import TopologyDefinition

from crczp.openstack_driver import CrczpOpenStackClient

TESTING_DATA_DIR = "assets"

TESTING_DEFINITION = "definition.yml"
TESTING_DEFINITION_EMPTY = "definition-empty.yml"
TESTING_TRANSFORMATION_CONFIGURATION = "trc-config.yml"
TESTING_GENERATED_HEAT_TEMPLATE = "generated-template.tf"
TESTING_BASE_NETWORK_TEMPLATE = "base-net-template.yml"


def data_path_join(file: str, data_dir: str = TESTING_DATA_DIR) -> str:
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), data_dir, file)


@pytest.fixture
def topology_definition():
    """Creates example topology definition."""
    with open(data_path_join(TESTING_DEFINITION)) as file:
        return TopologyDefinition.load(file)


@pytest.fixture
def topology_instance(topology_definition, trc):
    return TopologyInstance(topology_definition, trc)


@pytest.fixture
def empty_topology_definition():
    """Creates empty topology definition."""
    with open(data_path_join(TESTING_DEFINITION_EMPTY)) as file:
        return TopologyDefinition.load(file)


@pytest.fixture
def trc():
    """Creates transformation configuration."""
    path = data_path_join(TESTING_TRANSFORMATION_CONFIGURATION)
    return TransformationConfiguration.from_file(path)


@pytest.fixture
def generated_terraform_template():
    """Creates generated HEAT template"""
    with open(data_path_join(TESTING_GENERATED_HEAT_TEMPLATE)) as file:
        return yaml.safe_load(file)


@pytest.fixture
def base_network_template():
    with open(data_path_join(TESTING_BASE_NETWORK_TEMPLATE)) as file:
        return file.read()


@pytest.fixture()
def os_client(mocker, trc):
    kwargs = {
        "auth_url": mocker.MagicMock(),
        "application_credential_id": mocker.MagicMock(),
        "application_credential_secret": mocker.MagicMock(),
        "trc": trc,
    }

    return CrczpOpenStackClient(**kwargs)
