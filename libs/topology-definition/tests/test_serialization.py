import os

import pytest

from kypo.topology.definition.yaml import reader


@pytest.fixture
def topology_definition():
    return reader.read_topology_definition(os.path.join(os.path.dirname(__file__), 'assets/sandbox.yml'))


@pytest.mark.integration
class TestDummy:

    def test_read_yaml(self, topology_definition):
        assert topology_definition is not None
        assert len(topology_definition.hosts) == 2
        assert not topology_definition.find_network_by_name('home-switch').accessible_by_user

    def test_indexes(self, topology_definition):
        assert topology_definition.find_host_by_name('server') is not None
        assert topology_definition.find_host_by_name('home') is not None
