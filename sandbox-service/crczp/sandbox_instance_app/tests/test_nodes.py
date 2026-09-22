"""Tests for VM node actions and retrieval."""

import pytest

from crczp.cloud_commons import TopologyInstance, TransformationConfiguration
from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_instance_app.lib import nodes
from crczp.topology_definition.models import TopologyDefinition

MULTIHOMED_DEFINITION = """
name: multihomed-sandbox
hosts:
  - name: victim
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small

routers:
  - name: gw
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small

wan:
  name: internet
  cidr: 100.100.100.0/29

networks:
  - name: denying-lan
    cidr: 10.10.60.0/24
    accessible_by_user: false

  - name: admitting-lan
    cidr: 10.10.70.0/24
    accessible_by_user: true

  - name: gw-only-lan
    cidr: 10.10.80.0/24
    accessible_by_user: false

net_mappings:
  - host: victim
    network: denying-lan
    ip: 10.10.60.5

  - host: victim
    network: admitting-lan
    ip: 10.10.70.5

router_mappings:
  - router: gw
    network: gw-only-lan
    ip: 10.10.80.1

groups: []
"""


@pytest.fixture
def multihomed_topology_instance() -> TopologyInstance:
    """
    A host attached to a denying network and an admitting one, and a router attached
    only to a denying network besides the WAN.
    """
    topology_definition = TopologyDefinition.load(MULTIHOMED_DEFINITION)
    trc_config = TransformationConfiguration(
        man_image='debian-12-x86_64', man_flavor='standard.small', man_user='debian'
    )
    ti = TopologyInstance(topology_definition, trc_config)
    for link in ti.get_links():
        if link.network is ti.wan:
            link.ip = f'{link.node.name}-wan-ip'
        elif link.network.name == 'denying-lan':
            link.ip = '10.10.60.5'
        elif link.network.name == 'admitting-lan':
            link.ip = '10.10.70.5'
    return ti


class TestNodeAction:
    """Tests for node_action function."""

    @pytest.mark.parametrize(
        'action',
        [
            'resume',
            'reboot',
        ],
    )
    def test_node_action_success(self, mocker, action):
        """Test that a valid node action calls the correct client method."""
        mock_client = mocker.patch('crczp.sandbox_common_lib.utils.get_terraform_client')
        mock_instance = mock_client.return_value
        action_dict = {
            'resume': mock_instance.resume_node,
            'reboot': mock_instance.reboot_node,
        }

        sb_mock = mocker.MagicMock()
        node_name = 'node_name'
        nodes.node_action(sb_mock, node_name, action)

        action_dict[action].assert_called_once_with(
            sb_mock.allocation_unit.get_stack_name.return_value, node_name
        )

    def test_node_action_unknown_action(self, mocker):
        """Test that an unknown action raises ValidationError."""
        mocker.patch('crczp.sandbox_common_lib.utils.get_terraform_client')
        with pytest.raises(exceptions.ValidationError):
            nodes.node_action(mocker.MagicMock(), 'node_name', 'non-action')


class TestGetNode:  # pylint: disable=too-few-public-methods
    """Tests for get_node function."""

    def test_get_node(self, mocker):
        """Test that get_node returns the client's get_node result."""
        mock_client = mocker.patch('crczp.terraform_driver.CrczpTerraformClient.get_node')
        result = nodes.get_node(mocker.MagicMock(), 'node_name')
        assert result == mock_client.return_value


class TestGetNodeIp:
    """Tests for _get_node_ip examining every attachment before refusing."""

    def test_one_admitting_attachment_suffices(self, multihomed_topology_instance):
        """A denying attachment is passed over when a later one admits."""
        victim = multihomed_topology_instance.get_node('victim')
        ip = nodes._get_node_ip(  # pylint: disable=protected-access
            multihomed_topology_instance, victim, frozenset()
        )
        assert ip == '10.10.70.5'

    def test_refusal_requires_every_attachment_to_deny(self, multihomed_topology_instance):
        """The request is refused, naming the node, only when every attachment denies."""
        gw = multihomed_topology_instance.get_node('gw')
        # Strip the WAN link's ip so the router's only remaining, denying attachment
        # (gw-only-lan) is what decides this case.
        for link in multihomed_topology_instance.get_links():
            if link.node is gw and link.network is multihomed_topology_instance.wan:
                link.ip = None

        with pytest.raises(exceptions.ValidationError, match='gw'):
            nodes._get_node_ip(  # pylint: disable=protected-access
                multihomed_topology_instance, gw, frozenset()
            )

    def test_wan_attachment_still_yields_an_address(self, multihomed_topology_instance):
        """A router's WAN address is returned unconditionally, even when every
        author-declared network denies the requesting user."""
        gw = multihomed_topology_instance.get_node('gw')
        ip = nodes._get_node_ip(  # pylint: disable=protected-access
            multihomed_topology_instance, gw, frozenset()
        )
        assert ip == 'gw-wan-ip'


class TestGetNodeAccessData:  # pylint: disable=too-few-public-methods
    """Tests for get_node_access_data threading users_roles into the host IP lookup."""

    def test_returns_the_admitting_attachment_address(self, mocker, multihomed_topology_instance):
        """get_node_access_data resolves host_ip via the same examine-every-attachment rule."""
        mocker.patch(
            'crczp.sandbox_instance_app.lib.nodes.get_node_available_protocols', return_value=[]
        )
        victim = multihomed_topology_instance.get_node('victim')
        access_data = nodes.get_node_access_data(multihomed_topology_instance, victim, frozenset())
        assert access_data.host_ip == '10.10.70.5'
