import io
import os

import pytest

from kypo.topology_definition.models import TopologyDefinition, Protocol

SANDBOX_DEFINITION_PATH = os.path.join(os.path.dirname(__file__), 'assets/sandbox.yml')


@pytest.fixture
def topology_definition_string():
    with open(SANDBOX_DEFINITION_PATH) as f:
        return f.read()


@pytest.fixture
def topology_definition():
    return TopologyDefinition.from_file(SANDBOX_DEFINITION_PATH)


@pytest.mark.integration
class TestDummy:

    def test_read_yaml(self, topology_definition):
        assert topology_definition is not None
        assert len(topology_definition.hosts) == 2
        assert not topology_definition.find_network_by_name('home-switch').accessible_by_user
        assert topology_definition.find_host_by_name('server').base_box.mng_protocol == Protocol.SSH
        assert topology_definition.find_host_by_name('home').base_box.mng_protocol == Protocol.WINRM

    def test_read_yaml_bad_provider(self, topology_definition):
        with open(SANDBOX_DEFINITION_PATH) as f:
            sb_def = f.read().replace('provider: OpenStack', 'provider: Invalid')
        strio = io.StringIO(sb_def)
        with pytest.raises(ValueError):
            TopologyDefinition.load(strio)

    def test_indexes(self, topology_definition):
        assert topology_definition.find_host_by_name('server') is not None
        assert topology_definition.find_host_by_name('home') is not None

    def test_read_yaml_bad_protocol(self, topology_definition_string):
        bad_topology_definition_string = topology_definition_string\
            .replace('winrm', 'InvalidProtocol')

        with pytest.raises(ValueError):
            TopologyDefinition.load(bad_topology_definition_string)
