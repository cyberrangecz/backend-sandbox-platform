"""Tests for sandbox management functions."""

import zipfile
from unittest import mock

import pytest
from django.db import IntegrityError
from django.http import Http404

from crczp.cloud_commons import UNIVERSAL_ROLES
from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_instance_app import serializers
from crczp.sandbox_instance_app.lib import sandboxes, sshconfig
from crczp.sandbox_instance_app.models import Sandbox, SandboxAllocationUnit, SandboxRoleKeypair
from crczp.sandbox_instance_app.tests.test_sshconfig import TestRoleAwareUserSshConfig

pytestmark = pytest.mark.django_db

SANDBOX_ID = 1
SANDBOX_UUID = '1'


class TestGetSandbox:
    """Tests for the get_sandbox retrieval function."""

    def test_get_sandbox_success(self):
        """Test successful sandbox retrieval by ID."""
        assert sandboxes.get_sandbox(SANDBOX_ID).id == SANDBOX_UUID

    def test_get_sandbox_404(self):
        """Test that a non-existent sandbox raises Http404."""
        with pytest.raises(Http404):
            sandboxes.get_sandbox(-1)

    def test_id_generation_raises(self, created_by):
        """Test that creating a Sandbox without an ID raises IntegrityError."""
        au: SandboxAllocationUnit = SandboxAllocationUnit.objects.create(
            pool_id=1, created_by=created_by
        )
        with pytest.raises(IntegrityError):
            Sandbox.objects.create(allocation_unit=au)

    def test_id_generation_success(self, created_by):
        """Test that creating a Sandbox with an explicit ID succeeds."""
        au: SandboxAllocationUnit = SandboxAllocationUnit.objects.create(
            pool_id=1, created_by=created_by
        )
        sandbox: Sandbox = Sandbox.objects.create(id=64, allocation_unit=au, ready=True)
        assert sandboxes.get_sandbox(sandbox.id) is not None


class TestGenerateRoleKeypairs:
    """Tests for generating a sandbox's per-role SSH keypairs."""

    def test_creates_one_keypair_per_declared_role(self, mocker, sandbox):
        """One SandboxRoleKeypair row is created per role the definition declares."""
        mocker.patch(
            'crczp.sandbox_instance_app.lib.sandboxes.definitions.get_definition',
            return_value=mock.MagicMock(get_declared_roles=lambda: {'red-team', 'blue-team'}),
        )

        sandboxes.generate_role_keypairs(sandbox, sandbox.allocation_unit.pool)

        roles = set(sandbox.role_keypairs.values_list('role', flat=True))
        assert roles == {'red-team', 'blue-team'}

    def test_declaring_no_role_creates_no_keypair(self, mocker, sandbox):
        """A role-free definition creates no SandboxRoleKeypair rows."""
        mocker.patch(
            'crczp.sandbox_instance_app.lib.sandboxes.definitions.get_definition',
            return_value=mock.MagicMock(get_declared_roles=set),
        )

        sandboxes.generate_role_keypairs(sandbox, sandbox.allocation_unit.pool)

        assert not sandbox.role_keypairs.exists()

    def test_rerun_for_an_already_generated_role_is_a_no_op(self, mocker, sandbox):
        """A role with an existing row is left untouched by a rerun."""
        mocker.patch(
            'crczp.sandbox_instance_app.lib.sandboxes.definitions.get_definition',
            return_value=mock.MagicMock(get_declared_roles=lambda: {'red-team'}),
        )
        existing = SandboxRoleKeypair.objects.create(
            sandbox=sandbox, role='red-team', private_key='priv', public_key='pub'
        )

        sandboxes.generate_role_keypairs(sandbox, sandbox.allocation_unit.pool)

        assert sandbox.role_keypairs.count() == 1
        existing.refresh_from_db()
        assert existing.private_key == 'priv'
        assert existing.public_key == 'pub'


class TestSandboxesManipulation:
    """Tests for sandbox locking, topology, and SSH config operations."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker, top_ins):
        """Patch get_topology_instance to return a test topology instance."""
        self.mock_get_top_ins = mocker.patch(
            'crczp.sandbox_instance_app.lib.sandboxes.get_topology_instance'
        )
        self.mock_get_top_ins.return_value = top_ins
        yield

    def test_lock_sandbox_success_anonymous_user(self):
        """Test locking a sandbox with an anonymous user succeeds."""
        sandbox = sandboxes.get_sandbox(SANDBOX_ID)
        assert sandboxes.lock_sandbox(sandbox, None).sandbox.id == sandbox.id

    def test_lock_sandbox_already_locked_anonymous_user(self, mocker):
        """Test that locking an already-locked sandbox raises ValidationError."""
        mocker.patch('crczp.sandbox_instance_app.lib.sandboxes.Sandbox')
        with pytest.raises(exceptions.ValidationError):
            sandboxes.lock_sandbox(mocker.Mock(), None)

    def test_lock_sandbox_success(self, created_by):
        """Test locking a sandbox with an authenticated user succeeds."""
        sandbox = sandboxes.get_sandbox(SANDBOX_ID)
        assert sandboxes.lock_sandbox(sandbox, created_by).sandbox.id == sandbox.id

    def test_lock_sandbox_already_locked(self, mocker, created_by):
        """Test that locking an already-locked sandbox with a user raises ValidationError."""
        mocker.patch('crczp.sandbox_instance_app.lib.sandboxes.Sandbox')
        with pytest.raises(exceptions.ValidationError):
            sandboxes.lock_sandbox(mocker.Mock(), created_by)

    def test_get_sandbox_topology(self, mocker, topology, image):
        """Test that get_sandbox_topology returns correctly serialized topology data."""
        mock_images = mocker.patch('crczp.terraform_driver.CrczpTerraformClient.list_images')
        mock_images.return_value = [image]

        with (
            mock.patch('django.core.cache.cache.get') as mock_cache_get,
            mock.patch('django.core.cache.cache.set'),
        ):
            mock_cache_get.return_value = None
            topo = sandboxes.get_sandbox_topology(mocker.Mock(), UNIVERSAL_ROLES)

        result = serializers.TopologySerializer(topo).data

        assert sorted(topology['routers'], key=lambda x: x['name']) == sorted(
            result['routers'], key=lambda x: x['name']
        )

    def test_get_user_ssh_config(self, mocker, user_ssh_config):
        """Test that get_user_sshconfig returns the expected SSH config."""
        sandbox = mocker.MagicMock()
        sandbox.allocation_unit.get_stack_name.return_value = 'stack-name'

        ssh_conf = sandboxes.get_user_sshconfig(sandbox, UNIVERSAL_ROLES)
        assert ssh_conf.asdict() == user_ssh_config.asdict()

    def test_get_user_ssh_access(self, mocker, sandbox, user_ssh_config):
        """Test that get_user_ssh_access returns a zip containing the SSH config and keys."""
        sandbox.allocation_unit.get_stack_name = mocker.MagicMock()
        sandbox.allocation_unit.get_stack_name.return_value = 'stack-name'
        ssh_access_name = f'pool-id-{sandbox.allocation_unit.pool.id}-sandbox-id-{sandbox.id}-user'
        ssh_config_name = f'{ssh_access_name}-config'
        private_key = f'{ssh_access_name}-key'

        for host in user_ssh_config.hosts:
            identity_file = host.get('IdentityFile')
            host.set(
                'IdentityFile',
                identity_file.replace('<path_to_sandbox_private_key>', f'~/.ssh/{private_key}'),
            )

        in_memory_zip_file = sandboxes.get_user_ssh_access(sandbox, UNIVERSAL_ROLES)

        with zipfile.ZipFile(in_memory_zip_file, 'r', zipfile.ZIP_DEFLATED) as zip_file:
            with zip_file.open(ssh_config_name) as file:
                assert (
                    sshconfig.CrczpSSHConfig.from_str(file.read().decode('utf-8')).asdict()
                    == user_ssh_config.asdict()
                )
            with zip_file.open(private_key) as file:
                assert file.read().decode('utf-8') == sandbox.private_user_key
            with zip_file.open(f'{private_key}.pub') as file:
                assert file.read().decode('utf-8') == sandbox.public_user_key

    def test_get_user_ssh_access_role_free_zip_carries_no_role_keys(self, mocker, sandbox):
        """A role-free sandbox's zip carries only the flat key, no role-scoped ones."""
        sandbox.allocation_unit.get_stack_name = mocker.MagicMock()
        sandbox.allocation_unit.get_stack_name.return_value = 'stack-name'
        ssh_access_name = f'pool-id-{sandbox.allocation_unit.pool.id}-sandbox-id-{sandbox.id}-user'
        private_key = f'{ssh_access_name}-key'

        in_memory_zip_file = sandboxes.get_user_ssh_access(sandbox, frozenset())

        with zipfile.ZipFile(in_memory_zip_file, 'r', zipfile.ZIP_DEFLATED) as zip_file:
            names = zip_file.namelist()
            assert private_key in names
            assert not any(name.startswith(f'{private_key}-') for name in names)

    @pytest.mark.parametrize(
        'users_roles',
        [frozenset(), frozenset({'red-team'}), UNIVERSAL_ROLES],
    )
    def test_get_user_ssh_access_identity_files_all_exist_in_the_zip(
        self, mocker, sandbox, users_roles
    ):
        """Every IdentityFile the generated config names is a key file in the same zip."""
        self.mock_get_top_ins.return_value = TestRoleAwareUserSshConfig._build_topology_instance()
        sandbox.allocation_unit.get_stack_name = mocker.MagicMock()
        sandbox.allocation_unit.get_stack_name.return_value = 'stack-name'
        ssh_access_name = f'pool-id-{sandbox.allocation_unit.pool.id}-sandbox-id-{sandbox.id}-user'
        SandboxRoleKeypair.objects.create(
            sandbox=sandbox,
            role='red-team',
            private_key='red-team-private',
            public_key='red-team-public',
        )

        in_memory_zip_file = sandboxes.get_user_ssh_access(sandbox, users_roles)

        with zipfile.ZipFile(in_memory_zip_file, 'r', zipfile.ZIP_DEFLATED) as zip_file:
            names = set(zip_file.namelist())
            config = sshconfig.CrczpSSHConfig.from_str(
                zip_file.read(f'{ssh_access_name}-config').decode('utf-8')
            )
            identity_files = {
                host.get('IdentityFile').removeprefix('~/.ssh/') for host in config.hosts
            }
            assert identity_files <= names

    def test_get_user_ssh_access_carries_one_key_per_held_role(self, mocker, sandbox):
        """The zip carries the flat key plus one private-key file per role held."""
        sandbox.allocation_unit.get_stack_name = mocker.MagicMock()
        sandbox.allocation_unit.get_stack_name.return_value = 'stack-name'
        ssh_access_name = f'pool-id-{sandbox.allocation_unit.pool.id}-sandbox-id-{sandbox.id}-user'
        private_key = f'{ssh_access_name}-key'

        SandboxRoleKeypair.objects.create(
            sandbox=sandbox,
            role='red-team',
            private_key='red-team-private',
            public_key='red-team-public',
        )
        SandboxRoleKeypair.objects.create(
            sandbox=sandbox,
            role='blue-team',
            private_key='blue-team-private',
            public_key='blue-team-public',
        )

        in_memory_zip_file = sandboxes.get_user_ssh_access(sandbox, frozenset({'red-team'}))

        with zipfile.ZipFile(in_memory_zip_file, 'r', zipfile.ZIP_DEFLATED) as zip_file:
            names = zip_file.namelist()
            assert private_key in names
            assert f'{private_key}-red-team' in names
            assert f'{private_key}-blue-team' not in names
            with zip_file.open(f'{private_key}-red-team') as file:
                assert file.read().decode('utf-8') == 'red-team-private'

    def test_get_user_ssh_access_universal_roles_carries_every_role_key(self, mocker, sandbox):
        """A privileged requester's zip carries every declared role's key."""
        sandbox.allocation_unit.get_stack_name = mocker.MagicMock()
        sandbox.allocation_unit.get_stack_name.return_value = 'stack-name'
        ssh_access_name = f'pool-id-{sandbox.allocation_unit.pool.id}-sandbox-id-{sandbox.id}-user'
        private_key = f'{ssh_access_name}-key'

        SandboxRoleKeypair.objects.create(
            sandbox=sandbox,
            role='red-team',
            private_key='red-team-private',
            public_key='red-team-public',
        )
        SandboxRoleKeypair.objects.create(
            sandbox=sandbox,
            role='blue-team',
            private_key='blue-team-private',
            public_key='blue-team-public',
        )

        in_memory_zip_file = sandboxes.get_user_ssh_access(sandbox, UNIVERSAL_ROLES)

        with zipfile.ZipFile(in_memory_zip_file, 'r', zipfile.ZIP_DEFLATED) as zip_file:
            names = zip_file.namelist()
            assert f'{private_key}-red-team' in names
            assert f'{private_key}-blue-team' in names

    def test_get_management_ssh_config(self, mocker, management_ssh_config):
        """Test that get_management_sshconfig returns the expected SSH config."""
        sandbox = mocker.Mock()
        sandbox.allocation_unit.pool.get_pool_prefix.return_value = 'pool-prefix'

        ssh_conf = sandboxes.get_management_sshconfig(sandbox)

        assert ssh_conf.asdict() == management_ssh_config.asdict()

    def test_get_ansible_ssh_config(self, mocker, ansible_ssh_config):
        """Test that get_ansible_sshconfig returns the expected SSH config."""
        ssh_conf = sandboxes.get_ansible_sshconfig(
            mocker.Mock(), mng_key='/root/.ssh/pool_mng_key', proxy_key='/root/.ssh/id_rsa'
        )
        assert ssh_conf.asdict() == ansible_ssh_config.asdict()
