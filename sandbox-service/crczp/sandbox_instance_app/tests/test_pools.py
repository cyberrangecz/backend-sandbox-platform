"""Tests for sandbox instance pool operations."""

import zipfile

import pytest
from botocore.exceptions import ClientError
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from rest_framework.exceptions import ValidationError
from rest_framework.reverse import reverse
from rest_framework.test import APIRequestFactory

from crczp.cloud_commons import HardwareUsage, exceptions
from crczp.sandbox_common_lib.exceptions import ApiException, ForbiddenError, StackError
from crczp.sandbox_common_lib.exceptions import ValidationError as ApiValidationError
from crczp.sandbox_instance_app.lib import pools, sshconfig
from crczp.sandbox_instance_app.models import Pool, Sandbox, SandboxAllocationUnit, SandboxLock
from crczp.sandbox_instance_app.views import (
    PoolCleanupRequestUnlockedCreateView,
    PoolListCreateView,
    SandboxGetAndLockView,
)

pytestmark = pytest.mark.django_db

DEFINITION_ID = 1
POOL_ID = 1
FULL_POOL_ID = 2
SANDBOX_UUID = '1'


def client_error(operation: str) -> ClientError:
    """Return the error boto3 raises when an AWS operation is not permitted."""
    return ClientError(
        {'Error': {'Code': 'UnauthorizedOperation', 'Message': 'not authorized'}}, operation
    )


class TestCreatePool:
    """Tests for pool creation."""

    MAX_SIZE = 10

    @pytest.fixture(autouse=True)
    def set_up(self, mocker, image):  # pylint: disable=attribute-defined-outside-init
        """Set up mocks for pool creation tests."""
        self.client = mocker.patch('crczp.sandbox_common_lib.utils.get_terraform_client')
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.list_images', return_value=[image]
        )
        topology_definition = mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.get_definition'
        ).return_value
        topology_definition.network_forwarding = None
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.get_containers')
        mock_repo = mocker.patch('crczp.sandbox_definition_app.lib.definitions.get_def_provider')
        mock_repo.return_value.get_rev_sha = mocker.MagicMock(return_value='sha')
        self.arf = APIRequestFactory()
        yield

    def test_create_pool_success(self, definition, created_by, get_terraform_client):
        """Test that a pool is created successfully with valid parameters."""
        pool = pools.create_pool(
            {'definition_id': DEFINITION_ID, 'max_size': self.MAX_SIZE}, created_by=created_by
        )

        assert pool.max_size == self.MAX_SIZE
        assert pool.rev == definition.rev
        assert pool.definition.id == DEFINITION_ID

        stored = Pool.objects.get(pk=pool.pk)
        ssh_call, certificate_call = get_terraform_client.create_keypair.call_args_list
        assert stored.public_management_key == ssh_call.args[1] != ''
        assert stored.management_certificate == certificate_call.args[1] != ''
        assert stored.private_management_key

    def test_create_pool_invalid_definition(self, created_by):
        """Test that pool creation raises Http404 for an invalid definition ID."""
        with pytest.raises(Http404):
            pools.create_pool(
                {'definition_id': -1, 'max_size': self.MAX_SIZE}, created_by=created_by
            )

    @pytest.mark.parametrize(
        'error',
        [exceptions.CrczpException('bad topology'), client_error('DescribeImages')],
        ids=['crczp', 'cloud'],
    )
    @pytest.mark.usefixtures('definition')
    def test_create_pool_leaves_no_pool_when_validation_fails(
        self, created_by, get_terraform_client, error
    ):
        """Test that any definition validation failure leaves no Pool row and no key-pair."""
        get_terraform_client.validate_topology_definition.side_effect = error
        pool_count = Pool.objects.count()

        with pytest.raises(type(error)) as raised:
            pools.create_pool(
                {'definition_id': DEFINITION_ID, 'max_size': self.MAX_SIZE}, created_by=created_by
            )

        assert raised.value is error
        assert Pool.objects.count() == pool_count
        get_terraform_client.create_keypair.assert_not_called()

    @pytest.mark.usefixtures('definition')
    def test_create_pool_cleans_up_when_keypair_creation_fails(
        self, created_by, get_terraform_client
    ):
        """Test that a failed cloud key-pair import deletes the Pool row and its key-pairs."""
        get_terraform_client.create_keypair.side_effect = client_error('ImportKeyPair')
        pool_count = Pool.objects.count()

        with pytest.raises(ClientError, match='ImportKeyPair'):
            pools.create_pool(
                {'definition_id': DEFINITION_ID, 'max_size': self.MAX_SIZE}, created_by=created_by
            )

        assert Pool.objects.count() == pool_count
        ssh_keypair_name = get_terraform_client.create_keypair.call_args.args[0]
        get_terraform_client.delete_keypair.assert_any_call(ssh_keypair_name)

    @pytest.mark.usefixtures('definition')
    def test_create_pool_raises_original_error_when_cleanup_fails(
        self, mocker, created_by, get_terraform_client
    ):
        """Test that a failing key-pair cleanup is logged and does not hide the original error."""
        log = mocker.patch('crczp.sandbox_instance_app.lib.pools.LOG')
        get_terraform_client.create_keypair.side_effect = client_error('ImportKeyPair')
        get_terraform_client.delete_keypair.side_effect = client_error('DeleteKeyPair')
        pool_count = Pool.objects.count()

        with pytest.raises(ClientError, match='ImportKeyPair'):
            pools.create_pool(
                {'definition_id': DEFINITION_ID, 'max_size': self.MAX_SIZE}, created_by=created_by
            )

        assert Pool.objects.count() == pool_count
        log.warning.assert_called_once_with(
            'Pool removal after failed key-pair creation failed',
            pool_id=mocker.ANY,
            error=mocker.ANY,
        )

    @pytest.mark.usefixtures('definition', 'get_terraform_client')
    def test_create_pool_rolls_back_when_forwarding_disabled(
        self, mocker, created_by, forwarding_flag
    ):
        """Test that a forwarding definition is rejected with the flag off, leaving no Pool row."""
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.get_definition'
        ).return_value.network_forwarding = mocker.Mock()
        forwarding_flag(enabled=False)
        pool_count = Pool.objects.count()

        with pytest.raises(ApiValidationError, match='network_forwarding_enabled'):
            pools.create_pool(
                {'definition_id': DEFINITION_ID, 'max_size': self.MAX_SIZE}, created_by=created_by
            )

        assert Pool.objects.count() == pool_count

    @pytest.mark.usefixtures('definition', 'get_terraform_client')
    def test_create_pool_rolls_back_when_volumes_invalid(self, mocker, created_by):
        """Test that a definition failing the volume checks is rejected, leaving no Pool row."""
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.validate_volumes',
            side_effect=ApiValidationError('volume 0 is too small'),
        )
        pool_count = Pool.objects.count()

        with pytest.raises(ApiValidationError, match='volume 0 is too small'):
            pools.create_pool(
                {'definition_id': DEFINITION_ID, 'max_size': self.MAX_SIZE}, created_by=created_by
            )

        assert Pool.objects.count() == pool_count

    @pytest.mark.usefixtures('definition')
    def test_create_pool_leaves_no_pool_when_snapshot_lookup_fails(
        self, mocker, created_by, get_terraform_client
    ):
        """Test that a cloud error while looking up a volume snapshot leaves no Pool row."""
        mocker.patch.object(settings, 'AWS_PROVIDER_CONFIGURED', True)
        topology_definition = mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.get_definition'
        ).return_value
        topology_definition.network_forwarding = None
        topology_definition.hosts = [
            mocker.Mock(volumes=[mocker.Mock(image=None), mocker.Mock(image='snap-0123abcd')])
        ]
        get_terraform_client.get_snapshot_sizes.side_effect = client_error('DescribeSnapshots')
        pool_count = Pool.objects.count()

        with pytest.raises(ClientError, match='DescribeSnapshots'):
            pools.create_pool(
                {'definition_id': DEFINITION_ID, 'max_size': self.MAX_SIZE}, created_by=created_by
            )

        assert Pool.objects.count() == pool_count
        get_terraform_client.get_snapshot_sizes.assert_called_once_with(['snap-0123abcd'])

    def test_create_pool_invalid_size(self, created_by):
        """Test that pool creation raises ValidationError for an invalid pool size."""
        with pytest.raises(ValidationError):
            pools.create_pool({'definition_id': 1, 'max_size': -10}, created_by=created_by)

    def test_pool_views(self, mocker):
        """Test that the pool list view returns all pools."""
        mocker.patch(
            'crczp.sandbox_instance_app.lib.pools.get_hardware_usage_of_sandbox',
            return_value=HardwareUsage(**{
                'vcpu': 0.0,
                'ram': 0.0,
                'instances': 0.0,
                'network': 0.0,
                'subnet': 0.0,
                'port': 0.0,
            }),
        )
        request = self.arf.get(reverse('pool-list'))
        response = PoolListCreateView.as_view()(request)
        assert len(response.data['results']) == 2


class TestSandboxAllocationUnit:  # pylint: disable=too-few-public-methods
    """Tests for SandboxAllocationUnit model methods."""

    def test_get_stack_name(self):
        """Test that get_stack_name returns the correctly formatted stack name."""
        au: SandboxAllocationUnit = SandboxAllocationUnit.objects.get(id=1)
        prefix = settings.CRCZP_SERVICE_CONFIG.stack_name_prefix

        expected_stack_name = f'{prefix}-p{au.pool.id:010d}-s{au.id:010d}'
        assert au.get_stack_name() == expected_stack_name

        sb: Sandbox = Sandbox.objects.get(id=1)
        assert sb.allocation_unit.get_stack_name() == expected_stack_name


class TestCreateSandboxesInPool:
    """Tests for creating sandboxes within a pool."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker, definition):  # pylint: disable=attribute-defined-outside-init,unused-argument
        """Set up mocks for sandbox creation tests."""
        self.client = mocker.MagicMock()
        mock_get_client = mocker.patch('crczp.sandbox_common_lib.utils.get_terraform_client')
        mock_get_client.return_value = self.client
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.get_definition')
        self.fake_create_allocation_requests = mocker.patch(
            'crczp.sandbox_instance_app.lib.requests.create_allocations_requests'
        )

    def test_create_sandboxes_in_pool_success_one(self, created_by):
        """Test creating a single sandbox in a pool."""
        pool = pools.get_pool(POOL_ID)
        pools.create_sandboxes_in_pool(pool, created_by, 1)
        self.fake_create_allocation_requests.assert_called_once_with(pool, 1, created_by)

    def test_create_sandboxes_in_pool_success_all(self, created_by):
        """Test filling a pool with sandboxes up to its max size."""
        pool = pools.get_pool(POOL_ID)
        size_before = pool.size

        pools.create_sandboxes_in_pool(pool, created_by)
        self.fake_create_allocation_requests.assert_called_once_with(
            pool, pool.max_size - size_before, created_by
        )

    def test_create_sandboxes_in_pool_full(self, created_by):
        """Test that creating sandboxes in a full pool raises an error."""
        pool = pools.get_pool(FULL_POOL_ID)
        with pytest.raises(ApiException):
            pools.create_sandboxes_in_pool(pool, created_by, 1)

    def test_create_sandboxes_in_pool_limits_exceeded(self, created_by):
        """Test that sandbox creation fails when hardware limits are exceeded."""
        self.client.validate_hardware_usage_of_stacks.side_effect = exceptions.StackCreationFailed(
            'testException'
        )
        pool = pools.get_pool(POOL_ID)

        with pytest.raises(StackError):
            pools.create_sandboxes_in_pool(pool, created_by)


class TestGetUnlockedSandbox:
    """Tests for getting an unlocked sandbox from a pool."""

    def test_get_unlocked_sandbox_success_anonymous(self):
        """Test that an anonymous user can get an unlocked sandbox."""
        pool = pools.get_pool(POOL_ID)
        sb = pools.get_unlocked_sandbox(pool, None)
        assert sb is not None
        assert sb.id == SANDBOX_UUID
        assert sb.lock

    def test_get_unlocked_sandbox_empty_anonymous(self):
        """Test that None is returned when no unlocked sandbox is available (anonymous)."""
        pool = pools.get_pool(FULL_POOL_ID)
        sb = pools.get_unlocked_sandbox(pool, None)
        assert sb is None

    def test_get_unlocked_sandbox_success(self, created_by):
        """Test that an authenticated user can get an unlocked sandbox."""
        pool = pools.get_pool(POOL_ID)
        sb = pools.get_unlocked_sandbox(pool, created_by)
        assert sb is not None
        assert sb.id == SANDBOX_UUID
        assert sb.lock

    def test_get_unlocked_sandbox_empty(self, created_by):
        """Test that None is returned when no unlocked sandbox is available."""
        pool = pools.get_pool(FULL_POOL_ID)
        sb = pools.get_unlocked_sandbox(pool, created_by)
        assert sb is None

    def test_locks_the_sandbox_for_the_user(self, pool, sandbox, created_by):
        """Test that the free sandbox is locked by the requesting user."""
        sb = pools.get_unlocked_sandbox(pool, created_by)

        assert sb is not None
        assert sb.allocation_unit.pk == sandbox.allocation_unit.pk
        assert SandboxLock.objects.get(sandbox=sandbox).created_by == created_by

    def test_skips_a_sandbox_a_trainee_allocated(self, pool, sandbox):
        """Test that a trainee's own sandbox is never handed to someone else."""
        sandbox.allocation_unit.created_by_sub = 'trainee-sub'
        sandbox.allocation_unit.save()

        assert pools.get_unlocked_sandbox(pool, None) is None
        assert not SandboxLock.objects.filter(sandbox=sandbox).exists()

    def test_skips_a_locked_sandbox(self, pool, sandbox_lock):
        """Test that an already locked sandbox is not handed out again."""
        assert pools.get_unlocked_sandbox(pool, None) is None
        assert SandboxLock.objects.get(sandbox=sandbox_lock.sandbox) == sandbox_lock

    def test_user_holding_a_sandbox_gets_no_second_one(self, pool, sandbox, created_by):
        """Test that a user who already holds a sandbox of the pool is refused."""
        SandboxLock.objects.create(sandbox=sandbox, created_by=created_by)
        SandboxAllocationUnit.objects.create(pool=pool, created_by=created_by)

        with pytest.raises(exceptions.CrczpException, match='already have a sandbox'):
            pools.get_unlocked_sandbox(pool, created_by)


class TestValidateTrainingAccessToken:
    """Tests for checking a training access token against the pool lock."""

    def test_token_of_the_locking_training_passes(self, pool, pool_lock, training_access_token):  # pylint: disable=unused-argument
        """Test that the token the pool was locked with is accepted."""
        pools.validate_training_access_token(pool, training_access_token)

    def test_unlocked_pool_is_validation_error(self, pool, training_access_token):
        """Test that a pool without a lock is rejected as a bad request."""
        with pytest.raises(ApiValidationError, match='not locked'):
            pools.validate_training_access_token(pool, training_access_token)

    def test_lock_without_training_is_forbidden(self, pool, training_access_token):
        """Test that a pool an organizer locked by hand admits no training token."""
        pools.lock_pool(pool)

        with pytest.raises(ForbiddenError, match='does not have a training'):
            pools.validate_training_access_token(pool, training_access_token)

    @pytest.mark.parametrize('token', ['token-5768', '', None])
    def test_other_token_is_forbidden(self, pool, pool_lock, token):  # pylint: disable=unused-argument
        """Test that any other token, or none, is rejected."""
        with pytest.raises(ForbiddenError, match='not valid'):
            pools.validate_training_access_token(pool, token)


class TestPoolCleanupRequestUnlocked:
    """POST pools/{pool_id}/cleanup-unlocked."""

    def test_skips_sandboxes_trainees_allocated(self, mocker, pool, sandbox):
        """Test that unlocked trainee sandboxes are in use and are not cleaned up."""
        trainee_unit = SandboxAllocationUnit.objects.create(pool=pool, created_by_sub='trainee')
        Sandbox.objects.create(
            id='trainee-sandbox',
            allocation_unit=trainee_unit,
            private_user_key='private-key',
            public_user_key='public-key',
            ready=True,
        )
        create_cleanup_requests = mocker.patch(
            'crczp.sandbox_instance_app.views.sandbox_requests.create_cleanup_requests'
        )
        request = APIRequestFactory().post('/')

        response = PoolCleanupRequestUnlockedCreateView.as_view()(request, pool_id=pool.id)

        assert response.status_code == 201
        create_cleanup_requests.assert_called_once_with([sandbox.allocation_unit], False)


class TestGetManagementSSHAccess:
    """Tests for generating management SSH access configuration."""

    mock_get_top_ins = None
    mock_get_sandboxes_in_pool = None

    @pytest.fixture(autouse=True)
    def set_up(self, mocker, top_ins, sandbox):  # pylint: disable=attribute-defined-outside-init
        """Set up mocks for management SSH access tests."""
        self.mock_get_top_ins = mocker.patch(
            'crczp.sandbox_instance_app.lib.sandboxes.get_topology_instance'
        )
        self.mock_get_top_ins.return_value = top_ins

        self.mock_get_sandboxes_in_pool = mocker.patch(
            'crczp.sandbox_instance_app.lib.pools.get_sandboxes_in_pool'
        )
        self.mock_get_sandboxes_in_pool.return_value = [sandbox]
        yield

    def test_get_management_ssh_access_success(self, pool, sandbox, management_ssh_config, mocker):
        """Test that management SSH access returns a ZIP file with keys and config."""
        pool.get_pool_prefix = mocker.MagicMock()
        pool.get_pool_prefix.return_value = 'pool-prefix'
        ssh_access_name = f'pool-id-{pool.id}'
        ssh_config_name = f'{ssh_access_name}-sandbox-id-{sandbox.id}-management-config'
        private_key = f'{ssh_access_name}-management-key'

        for host in management_ssh_config.hosts:
            identity_file = host.get('IdentityFile')
            host.set(
                'IdentityFile',
                identity_file.replace('<path_to_pool_private_key>', f'~/.ssh/{private_key}'),
            )

        in_memory_zip_file = pools.get_management_ssh_access(pool)

        with zipfile.ZipFile(in_memory_zip_file, 'r', zipfile.ZIP_DEFLATED) as zip_file:
            with zip_file.open(ssh_config_name) as file:
                assert (
                    sshconfig.CrczpSSHConfig.from_str(file.read().decode('utf-8')).asdict()
                    == management_ssh_config.asdict()
                )
            with zip_file.open(private_key) as file:
                assert file.read().decode('utf-8') == pool.private_management_key
            with zip_file.open(f'{private_key}.pub') as file:
                assert file.read().decode('utf-8') == pool.public_management_key


class TestPoolLock:
    """Tests for pool lock and sandbox access views."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker, pool):  # pylint: disable=attribute-defined-outside-init
        """Set up the API request factory and a wrong training access token."""
        self.factory = APIRequestFactory()
        self.wrong_training_access_token = 'token-5768'
        mocker.patch('crczp.sandbox_common_lib.utils.get_object_or_404', return_value=pool)
        yield

    def test_sandbox_get_and_lock_view_correct_token_successful(  # pylint: disable=unused-argument
        self, pool, pool_lock, sandbox, training_access_token
    ):
        """Test that a correct training access token returns a locked sandbox."""
        request = self.factory.get(
            f'/pools/{pool.id}/sandboxes/get-and-lock/{training_access_token}'
        )
        request.user = AnonymousUser()

        view = SandboxGetAndLockView()
        view.kwargs = {'training_access_token': training_access_token, 'pool_id': pool.id}
        response = view.get(request)

        assert response.status_code == 200
        assert response.data['lock_id'] == sandbox.lock.id
        assert response.data['allocation_unit_id'] == sandbox.allocation_unit_id

    def test_sandbox_get_and_lock_view_incorrect_token_unsuccessful(  # pylint: disable=unused-argument
        self, pool, pool_lock, training_access_token
    ):
        """Test that an incorrect training access token returns 403."""
        request = self.factory.get(
            f'/pools/{pool.id}/sandboxes/get-and-lock/{self.wrong_training_access_token}'
        )

        response = SandboxGetAndLockView.as_view()(
            request, pool_id=pool.id, training_access_token=self.wrong_training_access_token
        )

        assert response.status_code == 403
        assert response.data == {'detail': 'Provided training access token is not valid.'}

    def test_sandbox_get_and_lock_view_unlocked_pool(self, pool, sandbox, training_access_token):  # pylint: disable=unused-argument
        """Test that a pool no training holds answers 400."""
        request = self.factory.get(
            f'/pools/{pool.id}/sandboxes/get-and-lock/{training_access_token}'
        )

        response = SandboxGetAndLockView.as_view()(
            request, pool_id=pool.id, training_access_token=training_access_token
        )

        assert response.status_code == 400
        assert response.data == {'detail': 'The pool is not locked.'}

    def test_sandbox_get_and_lock_view_correct_token_full_pool(  # pylint: disable=unused-argument,too-many-arguments,too-many-positional-arguments
        self, mocker, pool, pool_lock, sandbox, training_access_token
    ):
        """Test that 409 is returned when the pool is full and all sandboxes are locked."""
        response = None

        responses_list = [sandbox] * (pool.max_size - 1)
        responses_list.append(None)
        mocker.patch(
            'crczp.sandbox_instance_app.lib.pools.get_unlocked_sandbox', side_effect=responses_list
        )

        request = self.factory.get(
            f'/pools/{pool.id}/sandboxes/get-and-lock/{training_access_token}'
        )
        request.user = AnonymousUser()
        view = SandboxGetAndLockView()
        view.kwargs = {'training_access_token': training_access_token, 'pool_id': pool.id}

        for _ in range(pool.max_size):
            response = view.get(request)

        assert response is not None
        assert response.status_code == 409
