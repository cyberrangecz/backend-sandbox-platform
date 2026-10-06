"""Tests for VM node actions and retrieval."""

import copy

import pytest

from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_common_lib.crczp_config import OpenStackConsoleType
from crczp.sandbox_instance_app.lib import nodes


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


def test_get_console_url_enqueues_configured_console_type(mocker, settings):
    """The console type comes from the openstack section of the configuration."""
    config = copy.copy(settings.CRCZP_CONFIG)
    config.openstack = copy.copy(config.openstack)
    config.openstack.console_type = OpenStackConsoleType.NOVNC
    settings.CRCZP_CONFIG = config
    mocker.patch.object(nodes, 'cache').get.return_value = None
    enqueue = mocker.patch.object(nodes.django_rq, 'enqueue')

    assert nodes.get_console_url(mocker.MagicMock(), 'node_name') == ''

    enqueue.assert_called_once()
    assert enqueue.call_args.args[3] == 'novnc'


class TestGetNodeAccessData:
    """Tests for the host_ip of node access data."""

    @pytest.fixture(autouse=True)
    def _no_image_lookup(self, mocker):
        mocker.patch.object(nodes, 'get_node_available_protocols', return_value=[])

    def test_host_ip_is_main_link(self, top_ins_multi_homed):
        """A multi-homed host is reached on its first mapping, not its first network."""
        monitoring = top_ins_multi_homed.get_node('monitoring')

        access_data = nodes.get_node_access_data(top_ins_multi_homed, monitoring)

        assert access_data.host_ip == '10.10.20.6'

    def test_host_with_inaccessible_main_link_rejected(self, top_ins_multi_homed):
        """An inaccessible main network is rejected even if another network is accessible."""
        top_ins_multi_homed.get_network('server-switch').accessible_by_user = False
        monitoring = top_ins_multi_homed.get_node('monitoring')

        with pytest.raises(exceptions.ValidationError, match='not user-accessible'):
            nodes.get_node_access_data(top_ins_multi_homed, monitoring)
