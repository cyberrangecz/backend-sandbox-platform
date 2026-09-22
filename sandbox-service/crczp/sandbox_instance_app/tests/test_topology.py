"""Tests for topology serialization and Docker container topology."""

import pytest

from crczp.cloud_commons import UNIVERSAL_ROLES, TopologyInstance, TransformationConfiguration
from crczp.sandbox_instance_app import serializers
from crczp.sandbox_instance_app.lib.topology import Topology
from crczp.sandbox_instance_app.tests.conftest import mock_topology_cache
from crczp.topology_definition.models import TopologyDefinition

pytestmark = pytest.mark.django_db

ROLE_GATED_DEFINITION = """
name: topo-roles-sandbox
hosts:
  - name: victim
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small

  - name: secret
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small
    visible_by_roles: []

routers:
  - name: gw-a
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small

  - name: gw-b
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small

networks:
  - name: net-a
    cidr: 10.10.10.0/24
    accessible_by_roles: [red-team]

  - name: net-b
    cidr: 10.10.20.0/24
    accessible_by_user: true

net_mappings:
  - host: victim
    network: net-a
    ip: 10.10.10.5

  - host: victim
    network: net-b
    ip: 10.10.20.5

  - host: secret
    network: net-b
    ip: 10.10.20.6

router_mappings:
  - router: gw-a
    network: net-a
    ip: 10.10.10.1

  - router: gw-b
    network: net-b
    ip: 10.10.20.1

groups: []
"""


@pytest.fixture
def role_gated_topology_instance() -> TopologyInstance:
    """
    A visible host (victim) attached to a role-gated network and a universally
    accessible one, and a concealed host (secret) on the universally accessible one.
    """
    topology_definition = TopologyDefinition.load(ROLE_GATED_DEFINITION)
    trc_config = TransformationConfiguration(
        man_image='debian-12-x86_64', man_flavor='standard.small', man_user='debian'
    )
    return TopologyInstance(topology_definition, trc_config)


class TestTopology:
    """Tests for topology serialization from a topology instance."""

    def test_topology_success(self, mocker, top_ins, topology, image):
        """Test that a topology instance serializes correctly."""
        mock_images = mocker.patch('crczp.terraform_driver.CrczpTerraformClient.list_images')
        mock_images.return_value = [image]
        topo = mock_topology_cache(top_ins)

        result = serializers.TopologySerializer(topo).data

        assert sorted(topology['routers'], key=lambda x: x['name']) == sorted(
            result['routers'], key=lambda x: x['name']
        )

    def test_topology_hidden_success(self, mocker, top_ins_hidden, topology_hidden, image):
        """Test that hidden topology items are serialized correctly."""
        mock_images = mocker.patch('crczp.terraform_driver.CrczpTerraformClient.list_images')
        mock_images.return_value = [image]
        topo = mock_topology_cache(top_ins_hidden)

        result = serializers.TopologySerializer(topo).data

        assert sorted(topology_hidden['routers'], key=lambda x: x['name']) == sorted(
            result['routers'], key=lambda x: x['name']
        )


class TestDockerContainers:
    """Tests for Docker container entries in the topology object."""

    def test_docker_containers_in_topology_object(
        self, mocker, top_ins_with_containers, image, topology_containers
    ):
        """Test if containers are present in the Topology Visualization object."""
        mock_images = mocker.patch('crczp.terraform_driver.CrczpTerraformClient.list_images')
        mock_images.return_value = [image]

        topology = Topology(top_ins_with_containers, UNIVERSAL_ROLES)
        result = serializers.TopologySerializer(topology).data

        assert sorted(topology_containers['routers'], key=lambda x: x['name']) == sorted(
            result['routers'], key=lambda x: x['name']
        )

        # Docker container hosts must not appear in any subnet's host list
        all_subnet_host_names = [
            host['name']
            for router in result['routers']
            for subnet in router['subnets']
            for host in subnet['hosts']
        ]
        assert 'server' not in all_subnet_host_names

        for host in top_ins_with_containers.topology_definition.hosts:
            if host.name == 'server':
                assert host.hidden
            elif host.name == 'home':
                assert not host.hidden

    def test_server_host_is_hidden(
        self, mocker, top_ins_with_containers_with_server, image, topology_containers_server
    ):
        """Test that a server container host is marked hidden in the topology."""
        mock_images = mocker.patch('crczp.terraform_driver.CrczpTerraformClient.list_images')
        mock_images.return_value = [image]

        topology = Topology(top_ins_with_containers_with_server, UNIVERSAL_ROLES)
        result = serializers.TopologySerializer(topology).data

        assert sorted(topology_containers_server['routers'], key=lambda x: x['name']) == sorted(
            result['routers'], key=lambda x: x['name']
        )

        # Docker container hosts must not appear in any subnet's host list
        all_subnet_host_names = [
            host['name']
            for router in result['routers']
            for subnet in router['subnets']
            for host in subnet['hosts']
        ]
        assert 'server' not in all_subnet_host_names

        for host in top_ins_with_containers_with_server.topology_definition.hosts:
            if host.name == 'server':
                assert host.hidden
            elif host.name == 'home':
                assert not host.hidden


class TestRoleAwareTopologyData:
    """Tests for role-aware visibility and reach in the topology data payload."""

    @staticmethod
    def _hosts_by_subnet(topology: Topology) -> dict[str, list]:
        return {
            subnet.name: subnet.hosts for router in topology.routers for subnet in router.subnets
        }

    def test_concealment_removes_an_entity(self, mocker, role_gated_topology_instance, image):
        """A host whose visibility declaration denies the requester is absent."""
        mocker.patch(
            'crczp.terraform_driver.CrczpTerraformClient.list_images', return_value=[image]
        )
        topology = Topology(role_gated_topology_instance, frozenset())
        all_names = [
            host.name for hosts in self._hosts_by_subnet(topology).values() for host in hosts
        ]
        assert 'secret' not in all_names

    def test_no_reach_does_not_remove_an_entity(self, mocker, role_gated_topology_instance, image):
        """A visible host on a network that denies reach is still listed, marked not accessible."""
        mocker.patch(
            'crczp.terraform_driver.CrczpTerraformClient.list_images', return_value=[image]
        )
        topology = Topology(role_gated_topology_instance, frozenset())
        hosts_by_subnet = self._hosts_by_subnet(topology)

        victim_on_net_a = next(host for host in hosts_by_subnet['net-a'] if host.name == 'victim')
        assert victim_on_net_a.is_accessible is False

    def test_multihomed_host_reported_per_attachment(
        self, mocker, role_gated_topology_instance, image
    ):
        """A multihomed host appears once per visible network, each entry carrying that
        network's own reach."""
        mocker.patch(
            'crczp.terraform_driver.CrczpTerraformClient.list_images', return_value=[image]
        )
        topology = Topology(role_gated_topology_instance, frozenset())
        hosts_by_subnet = self._hosts_by_subnet(topology)

        victim_on_net_a = next(host for host in hosts_by_subnet['net-a'] if host.name == 'victim')
        victim_on_net_b = next(host for host in hosts_by_subnet['net-b'] if host.name == 'victim')
        assert victim_on_net_a.is_accessible is False
        assert victim_on_net_b.is_accessible is True

    def test_role_admits_reach_on_the_gated_network(
        self, mocker, role_gated_topology_instance, image
    ):
        """A requester holding the gating role is reported accessible on that network."""
        mocker.patch(
            'crczp.terraform_driver.CrczpTerraformClient.list_images', return_value=[image]
        )
        topology = Topology(role_gated_topology_instance, frozenset({'red-team'}))
        hosts_by_subnet = self._hosts_by_subnet(topology)

        victim_on_net_a = next(host for host in hosts_by_subnet['net-a'] if host.name == 'victim')
        assert victim_on_net_a.is_accessible is True
        assert 'secret' not in [host.name for host in hosts_by_subnet['net-b']]
