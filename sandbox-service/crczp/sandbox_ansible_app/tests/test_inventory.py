"""Tests for Ansible inventory generation."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from crczp.cloud_commons import TopologyInstance, TransformationConfiguration
from crczp.sandbox_ansible_app.lib.inventory import Inventory, Routing
from crczp.sandbox_common_lib import exceptions
from crczp.topology_definition.models import TopologyDefinition

MULTI_HOMED_DEFINITION = (
    Path(__file__).parents[2] / 'sandbox_instance_app/tests/assets/definition_multi_homed.yml'
)

pytestmark = pytest.mark.django_db


class TestCreateInventory:
    """Tests for Ansible inventory creation."""

    def test_create_inventory_success(self, top_ins, inventory):
        """Test that a valid inventory is generated for a standard topology."""
        ssh_public_mgmt_key = '/root/.ssh/pool_mng_key.pub'
        ssh_public_user_key = '/root/.ssh/user_key.pub'
        all_vars = {
            'global_sandbox_name': top_ins.name,
            'global_sandbox_ip': top_ins.ip,
            'global_sandbox_man_cidr': str(top_ins.man_network.cidr),
            'global_ssh_public_mgmt_key': ssh_public_mgmt_key,
            'global_ssh_public_user_key': ssh_public_user_key,
        }
        extra_vars = {'a': 1, 'b': 'b'}
        all_vars.update(extra_vars)
        inventory['all']['vars'] = all_vars

        result = Inventory(
            'pool-prefix',
            'stack-name',
            top_ins,
            '/root/.ssh/pool_mng_key',
            '/root/.ssh/pool_mng_cert',
            ssh_public_mgmt_key,
            ssh_public_user_key,
            extra_vars,
        )
        assert result.to_dict() == inventory

    def test_create_inventory_success_with_monitoring(
        self, top_ins_monitoring, inventory_monitoring
    ):
        """Test that a valid inventory is generated for a monitoring topology."""
        ssh_public_mgmt_key = '/root/.ssh/pool_mng_key.pub'
        ssh_public_user_key = '/root/.ssh/user_key.pub'
        all_vars = {
            'global_sandbox_name': top_ins_monitoring.name,
            'global_sandbox_ip': top_ins_monitoring.ip,
            'global_sandbox_man_cidr': str(top_ins_monitoring.man_network.cidr),
            'global_ssh_public_mgmt_key': ssh_public_mgmt_key,
            'global_ssh_public_user_key': ssh_public_user_key,
            'monitoring_http_targets': [
                {'url': 'https://example.com', 'check_string': 'Hello'},
                {'url': 'http://10.10.20.5:8080/status'},
            ],
        }
        extra_vars = {'a': 1, 'b': 'b'}
        all_vars.update(extra_vars)
        inventory_monitoring['all']['vars'] = all_vars

        result = Inventory(
            'pool-prefix',
            'stack-name',
            top_ins_monitoring,
            '/root/.ssh/pool_mng_key',
            '/root/.ssh/pool_mng_cert',
            ssh_public_mgmt_key,
            ssh_public_user_key,
            extra_vars,
        )

        assert result.to_dict() == inventory_monitoring

    def test_get_network_to_router_mapping(self, top_ins):
        """Test that the network-to-router mapping is correctly derived from the topology."""
        expected = {'home-switch': 'home-router', 'server-switch': 'server-router'}
        result = Routing._get_network_to_router_mapping(top_ins)  # pylint: disable=protected-access
        assert expected == result


class TestContainersInInventory:  # pylint: disable=too-few-public-methods
    """Tests for container entries in the Ansible inventory."""

    def test_containers_added_to_inventory(
        self, top_ins_with_containers, inventory, inventory_containers
    ):
        """Test that containers are included in the inventory when present."""
        ssh_public_mgmt_key = '/root/.ssh/pool_mng_key.pub'
        ssh_public_user_key = '/root/.ssh/user_key.pub'
        all_vars = {
            'global_sandbox_name': top_ins_with_containers.name,
            'global_sandbox_ip': top_ins_with_containers.ip,
            'global_sandbox_man_cidr': str(top_ins_with_containers.man_network.cidr),
            'global_ssh_public_mgmt_key': ssh_public_mgmt_key,
            'global_ssh_public_user_key': ssh_public_user_key,
        }
        extra_vars = {'a': 1, 'b': 'b'}
        all_vars.update(extra_vars)
        inventory['all']['vars'] = all_vars

        result = Inventory(
            'pool-prefix',
            'stack-name',
            top_ins_with_containers,
            '/root/.ssh/pool_mng_key',
            '/root/.ssh/pool_mng_cert',
            ssh_public_mgmt_key,
            ssh_public_user_key,
            extra_vars,
        )

        assert sorted(result.to_dict()) == sorted(inventory_containers)


class TestWindowsHostsGroup:
    """Tests for the windows_hosts group in the Ansible inventory."""

    def test_windows_hosts_group_created(self, top_ins):
        """Test that windows_hosts group is created with correct hosts."""
        ssh_public_mgmt_key = '/root/.ssh/pool_mng_key.pub'
        ssh_public_user_key = '/root/.ssh/user_key.pub'

        result = Inventory(
            'pool-prefix',
            'stack-name',
            top_ins,
            '/root/.ssh/pool_mng_key',
            '/root/.ssh/pool_mng_cert',
            ssh_public_mgmt_key,
            ssh_public_user_key,
        )

        # Check that windows_hosts group exists
        assert 'windows_hosts' in result.to_dict()['all']['children']

        # Check that 'home' is in windows_hosts (it uses windows-10 image)
        windows_hosts = result.to_dict()['all']['children']['windows_hosts']['hosts']
        assert 'home' in windows_hosts

        # Check that debian hosts are NOT in windows_hosts
        assert 'server' not in windows_hosts
        assert 'server-router' not in windows_hosts
        assert 'home-router' not in windows_hosts

    def test_windows_hosts_group_empty_when_no_windows(self, top_ins, mocker):
        """Test that windows_hosts group is not created when there are no Windows hosts."""
        # Mock list_images to return only Linux images
        mock_images = [
            mocker.MagicMock(name='debian-12-x86_64', os_type='linux'),
            mocker.MagicMock(name='windows-10', os_type='linux'),  # Pretend windows-10 is Linux
        ]
        mocker.patch(
            'crczp.sandbox_ansible_app.lib.group_builders.list_images', return_value=mock_images
        )

        ssh_public_mgmt_key = '/root/.ssh/pool_mng_key.pub'
        ssh_public_user_key = '/root/.ssh/user_key.pub'

        result = Inventory(
            'pool-prefix',
            'stack-name',
            top_ins,
            '/root/.ssh/pool_mng_key',
            '/root/.ssh/pool_mng_cert',
            ssh_public_mgmt_key,
            ssh_public_user_key,
        )

        # Check that windows_hosts group does not exist
        assert 'windows_hosts' not in result.to_dict()['all']['children']


class TestVpnEntrypointsGroup:
    """Tests for the vpn_entrypoints inventory group."""

    def test_vpn_entrypoints_group_created(self, top_ins_vpn):
        """Test that vpn_entrypoints group is created with the correct hosts and routers."""
        ssh_public_mgmt_key = '/root/.ssh/pool_mng_key.pub'
        ssh_public_user_key = '/root/.ssh/user_key.pub'

        result = Inventory(
            'pool-prefix',
            'stack-name',
            top_ins_vpn,
            '/root/.ssh/pool_mng_key',
            '/root/.ssh/pool_mng_cert',
            ssh_public_mgmt_key,
            ssh_public_user_key,
        )

        children = result.to_dict()['all']['children']
        assert 'vpn_entrypoints' in children
        vpn_hosts = children['vpn_entrypoints']['hosts']
        assert set(vpn_hosts.keys()) == {'server', 'server-router'}

    def test_vpn_entrypoints_group_absent_when_none_defined(self, top_ins):
        """Test vpn_entrypoints group is absent when the topology defines none."""
        ssh_public_mgmt_key = '/root/.ssh/pool_mng_key.pub'
        ssh_public_user_key = '/root/.ssh/user_key.pub'

        result = Inventory(
            'pool-prefix',
            'stack-name',
            top_ins,
            '/root/.ssh/pool_mng_key',
            '/root/.ssh/pool_mng_cert',
            ssh_public_mgmt_key,
            ssh_public_user_key,
        )

        assert 'vpn_entrypoints' not in result.to_dict()['all']['children']


def _inventory(top_ins) -> Inventory:
    return Inventory(
        'pool-prefix',
        'stack-name',
        top_ins,
        '/root/.ssh/pool_mng_key',
        '/root/.ssh/pool_mng_cert',
        '/root/.ssh/pool_mng_key.pub',
        '/root/.ssh/user_key.pub',
    )


def test_node_without_management_link_rejected(top_ins, mocker):
    """A node missing from the management network cannot be given an ansible_host."""
    links = top_ins.get_network_links
    mocker.patch.object(
        top_ins,
        'get_network_links',
        side_effect=lambda network: [link for link in links(network) if link.node.name != 'server'],
    )

    with pytest.raises(exceptions.AnsibleError, match='Management IP of node server is not known'):
        _inventory(top_ins)


def _server_def(top_ins):
    return next(h for h in top_ins.topology_definition.hosts if h.name == 'server')


class TestPasswordHost:
    """Tests for a host authenticating by password (e.g. an appliance without cloud-init)."""

    def test_password_authentication_vars(self, top_ins):
        """A host with a password gets ansible_password and ansible_become_password."""
        _server_def(top_ins).base_box.mgmt_password = 'test-password'  # nosec B105

        server_vars = _inventory(top_ins).to_dict()['all']['hosts']['server']

        assert server_vars['ansible_password'] == 'test-password'
        assert server_vars['ansible_become_password'] == 'test-password'
        assert 'ansible_connection' not in server_vars

    def test_host_without_password_has_no_password_vars(self, top_ins):
        """Without mgmt_password the inventory carries no password."""
        server_vars = _inventory(top_ins).to_dict()['all']['hosts']['server']

        assert 'ansible_password' not in server_vars
        assert 'ansible_become_password' not in server_vars

    def test_password_is_serialized_unsafe(self, top_ins):
        """A password with template syntax is tagged !unsafe so Ansible keeps it literal."""
        _server_def(top_ins).base_box.mgmt_password = 'te{{st'  # nosec B105

        serialized = _inventory(top_ins).serialize()

        assert "ansible_password: !unsafe 'te{{st'" in serialized
        assert "ansible_become_password: !unsafe 'te{{st'" in serialized

    @pytest.mark.parametrize('password', ['12345', 'test-password'])
    def test_password_without_template_syntax_is_a_plain_string(self, top_ins, password):
        """Only template syntax gets the !unsafe tag, which plain YAML loaders cannot read."""
        _server_def(top_ins).base_box.mgmt_password = password

        serialized = _inventory(top_ins).serialize()

        assert yaml.safe_load(serialized)['all']['hosts']['server']['ansible_password'] == password
        assert '!unsafe' not in serialized


class TestUnmanagedHostsGroup:
    """Tests for hosts that stage one must not configure."""

    def test_unmanaged_host_is_grouped_and_otherwise_unchanged(self, top_ins):
        """The host joins unmanaged_hosts but keeps its groups and variables."""
        baseline = _inventory(top_ins).to_dict()['all']
        _server_def(top_ins).managed = False

        result = _inventory(top_ins).to_dict()['all']

        children = result['children']
        assert children['unmanaged_hosts'] == {'hosts': {'server': None}}
        assert 'server' in children['ssh_nodes']['hosts']
        assert result['hosts']['server'] == baseline['hosts']['server']

    def test_unmanaged_hosts_group_is_empty_by_default(self, top_ins):
        """The group is always defined so that excluding it in a play pattern does not warn."""
        children = _inventory(top_ins).to_dict()['all']['children']

        assert children['unmanaged_hosts'] == {}


def _user_network_ips(
    definition: dict[str, Any], trc: TransformationConfiguration
) -> dict[str, str]:
    top_ins = TopologyInstance(TopologyDefinition.load(yaml.safe_dump(definition)), trc)
    top_ins.name = 'stack-name'
    top_ins.ip = '10.10.10.10'
    for number, link in enumerate(top_ins.get_network_links(top_ins.man_network), start=1):
        link.ip = f'192.168.128.{number}'
    hosts = _inventory(top_ins).to_dict()['all']['hosts']
    return {
        name: host_vars['user_network_ip']
        for name, host_vars in hosts.items()
        if 'user_network_ip' in host_vars
    }


class TestUserNetworkIp:
    """Tests for the user_network_ip variable of multi-homed nodes."""

    @pytest.mark.parametrize(
        ('sections', 'expected'),
        [
            ((), '10.10.20.6'),
            (('networks',), '10.10.20.6'),
            (('net_mappings',), '10.10.40.5'),
            (('net_mappings', 'networks'), '10.10.40.5'),
        ],
        ids=['unchanged', 'networks', 'net_mappings', 'both'],
    )
    def test_follows_net_mappings_order_not_networks_order(self, trc_config, sections, expected):
        """Reordering the host's net_mappings changes the IP; reordering networks does not."""
        definition = yaml.safe_load(MULTI_HOMED_DEFINITION.read_text(encoding='utf-8'))
        for section in sections:
            definition[section].reverse()

        assert _user_network_ips(definition, trc_config)['monitoring'] == expected
