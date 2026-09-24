"""Tests for the Ansible runner utilities."""

import pytest
from django.conf import settings

from crczp.sandbox_ansible_app.lib.ansible import AllocationAnsibleRunner
from crczp.sandbox_instance_app.lib import sandboxes
from crczp.sandbox_instance_app.models import Sandbox, SandboxNetbirdResources, SandboxRoleKeypair

pytestmark = pytest.mark.django_db


class TestPrepareInventoryFile:
    """Tests for Ansible inventory file preparation."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker, top_ins):  # pylint: disable=attribute-defined-outside-init
        """Set up mocks for each test."""
        self.client = mocker.patch('crczp.sandbox_common_lib.utils.get_terraform_client')
        self.client.get_sandbox.return_value = top_ins
        self.save_file = mocker.patch(
            'crczp.sandbox_ansible_app.lib.ansible.AnsibleRunner.save_file'
        )
        yield

    def test_prepare_inventory_file_success(self, mocker, top_ins):  # pylint: disable=unused-argument
        """Test that the inventory file is saved when preparing a successful allocation."""
        mock_inventory = mocker.patch('crczp.sandbox_ansible_app.lib.ansible.Inventory')
        mocker.patch.object(sandboxes, 'get_topology_instance', return_value=top_ins)

        dir_path = '/tmp'  # nosec B108
        sandbox = Sandbox.objects.get(pk=1)
        AllocationAnsibleRunner(dir_path).prepare_inventory_file(sandbox)

        mock_inventory.assert_called_once()

    def test_prepare_inventory_object(self, mocker, top_ins, inventory):
        """Test that create_inventory returns a correctly structured inventory dict."""
        mocker.patch.object(sandboxes, 'get_topology_instance', return_value=top_ins)
        dir_path = mocker.MagicMock()
        sandbox = Sandbox.objects.get(pk=1)
        sandbox.allocation_unit.pool.get_pool_prefix = mocker.MagicMock()
        sandbox.allocation_unit.pool.get_pool_prefix.return_value = 'pool-prefix'
        sandbox.allocation_unit.get_stack_name = mocker.MagicMock()
        sandbox.allocation_unit.get_stack_name.return_value = 'stack-name'
        result = AllocationAnsibleRunner(dir_path).create_inventory(sandbox)

        assert result.to_dict() == inventory

    def test_create_inventory_attaches_netbird_setup_key(self, mocker, top_ins_vpn):
        sandboxes.get_topology_instance = mocker.MagicMock()
        sandboxes.get_topology_instance.return_value = top_ins_vpn

        dir_path = mocker.MagicMock()
        sandbox = Sandbox.objects.get(pk=1)
        sandbox.allocation_unit.pool.get_pool_prefix = mocker.MagicMock()
        sandbox.allocation_unit.pool.get_pool_prefix.return_value = 'pool-prefix'
        sandbox.allocation_unit.get_stack_name = mocker.MagicMock()
        sandbox.allocation_unit.get_stack_name.return_value = 'stack-name'

        SandboxNetbirdResources.objects.create(
            sandbox=sandbox,
            entrypoint_host_name='server',
            host_setup_key_value='SK-TEST-123',
        )

        result = AllocationAnsibleRunner(dir_path).create_inventory(sandbox)

        vpn_group = result.get_group('vpn_entrypoints')
        assert vpn_group is not None
        assert vpn_group.hosts_vars['server'] == {'netbird_setup_key': 'SK-TEST-123'}

    def test_create_inventory_role_free_omits_public_user_keys_by_role(self, mocker, top_ins):
        """A role-free sandbox's inventory carries no global_ssh_public_user_keys_by_role."""
        mocker.patch.object(sandboxes, 'get_topology_instance', return_value=top_ins)
        dir_path = mocker.MagicMock()
        sandbox = Sandbox.objects.get(pk=1)
        sandbox.allocation_unit.pool.get_pool_prefix = mocker.MagicMock(return_value='pool-prefix')
        sandbox.allocation_unit.get_stack_name = mocker.MagicMock(return_value='stack-name')

        result = AllocationAnsibleRunner(dir_path).create_inventory(sandbox)

        assert 'global_ssh_public_user_keys_by_role' not in result.to_dict()['all']['vars']

    def test_create_inventory_carries_public_user_keys_by_role(self, mocker, top_ins):
        """A role-declaring sandbox's inventory names each role's public-key container path."""
        mocker.patch.object(sandboxes, 'get_topology_instance', return_value=top_ins)
        dir_path = mocker.MagicMock()
        sandbox = Sandbox.objects.get(pk=1)
        sandbox.allocation_unit.pool.get_pool_prefix = mocker.MagicMock(return_value='pool-prefix')
        sandbox.allocation_unit.get_stack_name = mocker.MagicMock(return_value='stack-name')
        SandboxRoleKeypair.objects.create(
            sandbox=sandbox, role='red-team', private_key='priv-red', public_key='pub-red'
        )
        SandboxRoleKeypair.objects.create(
            sandbox=sandbox, role='blue-team', private_key='priv-blue', public_key='pub-blue'
        )

        runner = AllocationAnsibleRunner(dir_path)
        result = runner.create_inventory(sandbox)

        assert result.to_dict()['all']['vars']['global_ssh_public_user_keys_by_role'] == {
            'red-team': runner.container_ssh_path('user_key_red-team.pub'),
            'blue-team': runner.container_ssh_path('user_key_blue-team.pub'),
        }


class TestPrepareSshDir:
    """Tests for SSH directory preparation, role-scoped public-key files included."""

    def test_prepare_ssh_dir_writes_one_file_per_role(self, mocker):
        """Each declared role's public key is written alongside the flat one."""
        save_file = mocker.patch('crczp.sandbox_ansible_app.lib.ansible.AnsibleRunner.save_file')
        mocker.patch('crczp.sandbox_ansible_app.lib.ansible.AnsibleRunner._prepare_ssh_dir')
        mocker.patch.object(sandboxes, 'get_ansible_sshconfig', return_value='config')

        sandbox = Sandbox.objects.get(pk=1)
        SandboxRoleKeypair.objects.create(
            sandbox=sandbox, role='red-team', private_key='priv-red', public_key='pub-red'
        )

        runner = AllocationAnsibleRunner('/tmp')  # nosec B108
        runner.prepare_ssh_dir(sandbox.allocation_unit.pool, sandbox)

        written_paths = {call.args[0] for call in save_file.call_args_list}
        assert runner.host_ssh_path('user_key_red-team.pub') in written_paths
        role_file_contents = {
            call.args[0]: call.args[1]
            for call in save_file.call_args_list
            if call.args[0] == runner.host_ssh_path('user_key_red-team.pub')
        }
        assert role_file_contents[runner.host_ssh_path('user_key_red-team.pub')] == 'pub-red'

    def test_prepare_ssh_dir_role_free_writes_no_role_files(self, mocker):
        """A role-free sandbox writes only the flat public-key file, nothing role-scoped."""
        save_file = mocker.patch('crczp.sandbox_ansible_app.lib.ansible.AnsibleRunner.save_file')
        mocker.patch('crczp.sandbox_ansible_app.lib.ansible.AnsibleRunner._prepare_ssh_dir')
        mocker.patch.object(sandboxes, 'get_ansible_sshconfig', return_value='config')

        sandbox = Sandbox.objects.get(pk=1)
        runner = AllocationAnsibleRunner('/tmp')  # nosec B108
        runner.prepare_ssh_dir(sandbox.allocation_unit.pool, sandbox)

        written_paths = [call.args[0] for call in save_file.call_args_list]
        assert not any(
            'user_key_' in path and path != runner.host_ssh_path('user_key.pub')
            for path in written_paths
        )


class TestGenerateDockerfiles:
    """Tests for Dockerfile generation."""

    def test_dockerfile_fetched_from_pinned_rev_sha(self, mocker):
        """Dockerfiles are fetched from the pool's pinned rev_sha, not the branch rev."""
        mocker.patch('crczp.sandbox_ansible_app.lib.ansible.AnsibleRunner.make_dir')
        mocker.patch('crczp.sandbox_ansible_app.lib.ansible.AnsibleRunner.save_file')

        container_def = mocker.Mock(image=None, dockerfile='docker2/')
        container_def.name = 'docker2'
        mapping = mocker.Mock(host='home', container='docker2')
        top_ins = mocker.Mock()
        top_ins.containers.container_mappings = [mapping]
        top_ins.containers.containers = [container_def]
        mocker.patch.object(sandboxes, 'get_topology_instance', return_value=top_ins)

        mock_get_dockerfile = mocker.patch(
            'crczp.sandbox_ansible_app.lib.ansible.definitions.get_dockerfile',
            return_value='FROM debian',
        )

        sandbox = mocker.Mock()
        sandbox.allocation_unit.pool.definition.url = 'test-def-url'
        sandbox.allocation_unit.pool.definition.rev = 'branch-name'
        sandbox.allocation_unit.pool.rev_sha = 'resolved-sha-123'

        runner = AllocationAnsibleRunner('/tmp')  # nosec B108
        runner._generate_dockerfiles(sandbox)  # pylint: disable=protected-access

        mock_get_dockerfile.assert_called_once_with(
            'test-def-url', 'resolved-sha-123', settings.CRCZP_CONFIG, 'docker2/'
        )
