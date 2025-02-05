from contextlib import contextmanager

import keystoneauth1.session
import pytest
from crczp.cloud_commons import CrczpException, TopologyInstance
from crczp.topology_definition.models import Network, Host, NetworkMapping, BaseBox
from novaclient.v2 import client as nova_client

from crczp.openstack_driver import utils


class TestUtils:
    @staticmethod
    def raises(error):
        """Wrapper around pytest.raises to support None."""
        if error:
            return pytest.raises(error)
        else:

            @contextmanager
            def not_raises():
                try:
                    yield
                except Exception as e:
                    raise e

            return not_raises()

    @pytest.mark.parametrize(
        "client_type, expected_client, exception",
        [
            ("neutron", utils.neutron_client.Client, None),
            ("glance", utils.glance_client.Client, None),
            ("nova", nova_client.Client, None),
            ("badClient", nova_client.Client, ValueError),
            ("neutron", nova_client.Client, AssertionError),
        ],
    )
    def test_get_client(self, client_type, expected_client, exception):
        with self.raises(exception):
            client = utils.get_client(client_type, keystoneauth1.session.Session())
            assert isinstance(client, expected_client)

    def test_get_session(self):
        session = utils.get_session("testUrl", "testId", "testSecret")
        assert isinstance(session, keystoneauth1.session.Session)


class TestValidateTopologyInstanceNetworks:
    def test_validate_topology_instance_networks_success(self, empty_topology_definition, trc):
        empty_topology_definition.networks.append(Network("net1", "10.10.0.0/24", True, False))
        empty_topology_definition.networks.append(Network("net2", "147.251.0.0/16", True, False))

        topology_instance = TopologyInstance(empty_topology_definition, trc)
        utils.validate_topology_instance_networks(topology_instance)

    def test_validate_topology_instance_networks_collision(self, empty_topology_definition, trc):
        empty_topology_definition.networks.append(Network("net1", "147.251.0.0/16", True, False))
        empty_topology_definition.networks.append(Network("net2", "147.251.0.0/16", True, False))

        topology_instance = TopologyInstance(empty_topology_definition, trc)

        with pytest.raises(CrczpException):
            utils.validate_topology_instance_networks(topology_instance)

    def test_validate_topology_instance_networks_range(self, empty_topology_definition, trc):
        empty_topology_definition.networks.append(Network("net1", "147.251.0.0/16", True, False))
        base_box = BaseBox()
        base_box.image = ""
        empty_topology_definition.hosts.append(Host("host1", base_box, "", False, False, []))
        empty_topology_definition.net_mappings.append(NetworkMapping("host1", "net1", "147.250.0.0"))

        topology_instance = TopologyInstance(empty_topology_definition, trc)

        with pytest.raises(CrczpException):
            utils.validate_topology_instance_networks(topology_instance)
