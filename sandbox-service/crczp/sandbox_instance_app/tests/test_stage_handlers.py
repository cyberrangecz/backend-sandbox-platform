"""Tests for allocation and cleanup stage handlers."""

import datetime
from subprocess import TimeoutExpired  # noqa: S404
from typing import Any
from unittest.mock import call

import django_rq
import fakeredis
import pytest
import requests
from django.core.exceptions import ObjectDoesNotExist
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from docker.errors import DockerException
from docker.errors import NotFound as DockerContainerNotFound
from rq.job import JobStatus
from rq.timeouts import JobTimeoutException

from crczp.cloud_commons import CrczpException
from crczp.cloud_commons import exceptions as driver_exceptions
from crczp.sandbox_ansible_app.lib.container import DockerContainer
from crczp.sandbox_ansible_app.models import Container, NetworkingAnsibleAllocationStage
from crczp.sandbox_common_lib import exceptions as api_exceptions
from crczp.sandbox_instance_app.lib import stage_handlers
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    AllocationTerraformOutput,
    CleanupRequest,
    Pool,
    Sandbox,
    SandboxAllocationUnit,
    StackAllocationStage,
    StackCleanupStage,
    Stage,
)
from crczp.terraform_driver.terraform_exceptions import TerraformStateLocked

pytestmark = pytest.mark.django_db


def assert_db_stage(stage: Stage, start_time: datetime.datetime, failed: bool = False) -> None:
    """Assert that a stage's DB record has correct start/end times and failure state."""
    assert stage.start is not None
    assert stage.end is not None
    assert start_time < stage.start
    assert stage.start < stage.end
    assert stage.end < timezone.now()
    assert stage.failed == failed
    assert isinstance(stage.error_message, str) if failed else stage.error_message is None


def assert_db_cancelled(stage: Stage) -> None:
    """Assert that a stage's DB record is marked failed and finished, with an end time."""
    stage.refresh_from_db()
    assert stage.finished
    assert stage.failed
    assert stage.end is not None


@pytest.fixture
def fake_redis(monkeypatch):
    """Serve every django_rq connection from one fresh in-memory Redis."""
    connection = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    monkeypatch.setattr(django_rq, 'get_connection', lambda *args, **kwargs: connection)
    return connection


class TestAllocationStackStageHandler:
    """Tests for the Terraform stack allocation stage handler."""

    @pytest.fixture(autouse=True)
    def terraform_client(self, mocker, process):
        """Patch stage handler internals and return the mocked Terraform client."""
        mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.LOG')
        mocker.patch(
            'crczp.sandbox_instance_app.lib.stage_handlers.definitions.get_definition'
        ).return_value.network_forwarding = None
        mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.utils.get_terraform_client')
        client = mocker.Mock()
        stage_handlers.AllocationStackStageHandler._client = client
        client.get_process_output.return_value = ['output']
        client.create_stack.return_value = process
        stage_handlers.AllocationStackStageHandler._wait_for_process = mocker.Mock()
        return client

    def test_execute_success(self, now, allocation_stage_stack, process):
        """Test successful execution of the allocation stack stage."""
        handler = stage_handlers.AllocationStackStageHandler(allocation_stage_stack)

        handler.execute()

        assert allocation_stage_stack.terraformstack.stack_id == process.pid
        assert_db_stage(allocation_stage_stack, now, failed=False)

    def test_execute_failed_creation_request(self, now, allocation_stage_stack, terraform_client):
        """Test that a failed stack creation request marks the stage as failed."""
        handler = stage_handlers.AllocationStackStageHandler(allocation_stage_stack)
        terraform_client.create_stack.side_effect = driver_exceptions.StackCreationFailed(
            'error-message'
        )

        with pytest.raises(driver_exceptions.StackCreationFailed):
            handler.execute()

        with pytest.raises(ObjectDoesNotExist):
            assert allocation_stage_stack.terraformstack
        assert_db_stage(allocation_stage_stack, now, failed=True)

    def test_execute_forwarding_disabled(
        self, mocker, now, allocation_stage_stack, terraform_client, forwarding_flag
    ):
        """Test that a forwarding definition fails before create_stack when the flag is off."""
        mocker.patch(
            'crczp.sandbox_instance_app.lib.stage_handlers.definitions.get_definition'
        ).return_value.network_forwarding = mocker.Mock()
        forwarding_flag(enabled=False)
        handler = stage_handlers.AllocationStackStageHandler(allocation_stage_stack)

        with pytest.raises(api_exceptions.ValidationError, match='network_forwarding_enabled'):
            handler.execute()

        terraform_client.create_stack.assert_not_called()
        assert_db_stage(allocation_stage_stack, now, failed=True)

    def test_execute_volumes_invalid(self, mocker, now, allocation_stage_stack, terraform_client):
        """Test that a definition failing the volume checks fails before create_stack."""
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.validate_volumes',
            side_effect=api_exceptions.ValidationError('volume 0 is too small'),
        )
        handler = stage_handlers.AllocationStackStageHandler(allocation_stage_stack)

        with pytest.raises(api_exceptions.ValidationError, match='volume 0 is too small'):
            handler.execute()

        terraform_client.create_stack.assert_not_called()
        assert_db_stage(allocation_stage_stack, now, failed=True)

    def test_execute_skips_a_stage_cancelled_before_its_job_ran(
        self, mocker, allocation_stage_stack, terraform_client
    ):
        """A stage finished before its job ran is not executed, and its dependents go."""
        type(allocation_stage_stack).objects.filter(pk=allocation_stage_stack.pk).update(
            finished=True, failed=True
        )
        current_job = mocker.patch(
            'crczp.sandbox_instance_app.lib.stage_handlers.get_current_job'
        ).return_value

        stage_handlers.AllocationStackStageHandler(allocation_stage_stack).execute()

        terraform_client.create_stack.assert_not_called()
        current_job.delete_dependents.assert_called_once_with()
        assert allocation_stage_stack.start is None

    def test_a_job_timeout_ends_the_process_before_the_stage_finishes(
        self, now, allocation_stage_stack, terraform_client, process
    ):
        """The OpenTofu process cut short by the job timeout has exited once the stage ends."""
        stage_finished_while_running = []
        process.poll.return_value = None
        process.communicate.side_effect = lambda timeout: stage_finished_while_running.append(
            StackAllocationStage.objects.get(pk=allocation_stage_stack.pk).finished
        )
        terraform_client.get_process_output.side_effect = JobTimeoutException('timed out')
        handler = stage_handlers.AllocationStackStageHandler(allocation_stage_stack)

        with pytest.raises(JobTimeoutException):
            handler.execute()

        process.terminate.assert_called_once_with()
        assert stage_finished_while_running == [False]
        process.kill.assert_not_called()
        assert_db_stage(allocation_stage_stack, now, failed=True)


class TestOpenTofuProcessEnd:
    """Ending the OpenTofu process when the block running it is left."""

    @pytest.fixture(autouse=True)
    def quiet_log(self, mocker):
        """Silence stage handler logging."""
        mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.LOG')

    def test_a_process_outliving_the_grace_period_is_killed(self, process):
        """A process still running after the grace period is killed and reaped."""
        process.poll.return_value = None
        process.communicate.side_effect = TimeoutExpired('tofu', 1)

        with pytest.raises(JobTimeoutException), stage_handlers._process_ended_on_exit(process):
            raise JobTimeoutException('timed out')

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with()

    def test_an_exited_process_is_left_alone(self, process):
        """A process that has exited is neither stopped nor killed."""
        process.poll.return_value = 0

        with stage_handlers._process_ended_on_exit(process):
            pass

        process.terminate.assert_not_called()
        process.kill.assert_not_called()


class TestCleanupStackStageHandler:
    """Tests for the Terraform stack cleanup stage handler."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker, process):
        """Patch stage handler internals and return a mock process."""
        mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.LOG')
        mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.utils.get_terraform_client')
        stage_handlers.CleanupStackStageHandler._client = mocker.Mock()
        stage_handlers.CleanupStackStageHandler._client.get_process_output.return_value = ['output']
        stage_handlers.CleanupStackStageHandler._wait_for_process = mocker.Mock()

    @pytest.fixture
    def unit(self, cleanup_stage_stack):
        """The allocation unit of the stage, the only one of its pool."""
        unit = cleanup_stage_stack.cleanup_request.allocation_unit
        unit.pool.size = 1
        unit.pool.save()
        return unit

    @pytest.fixture
    def removal(self, mocker):
        """The by-name deletion and the workspace removal the stage delegates to."""
        removal = mocker.Mock()
        removal.delete_resources_by_name.return_value = []
        mocker.patch.multiple(
            stage_handlers.stack_removal,
            delete_resources_by_name=removal.delete_resources_by_name,
            delete_workspace=removal.delete_workspace,
            delete_stack_directory=removal.delete_stack_directory,
        )
        return removal

    @pytest.fixture
    def destroy(self, mocker):
        """The destroy through the OpenTofu state, which succeeds unless a test says otherwise."""
        return mocker.patch.object(
            stage_handlers.CleanupStackStageHandler, '_destroy_stack', return_value=True
        )

    @pytest.fixture
    def delete_pool(self, mocker):
        return mocker.patch('crczp.sandbox_instance_app.lib.pools.delete_pool')

    def test_execute_success(self, now, cleanup_stage_stack, unit, removal, destroy, delete_pool):
        """A destroy that succeeds deletes nothing by name and removes the unit."""
        handler = stage_handlers.CleanupStackStageHandler(cleanup_stage_stack)

        handler.execute()

        assert_db_stage(cleanup_stage_stack, now, failed=False)
        removal.delete_resources_by_name.assert_not_called()
        removal.delete_workspace.assert_called_once_with(
            handler._client, unit.get_stack_name(), force=False
        )
        removal.delete_stack_directory.assert_called_once_with(
            handler._client, unit.get_stack_name()
        )
        assert not SandboxAllocationUnit.objects.filter(pk=unit.pk).exists()
        assert Pool.objects.get(pk=unit.pool_id).size == 0
        delete_pool.assert_not_called()

    @pytest.mark.parametrize(
        'destroy_effect',
        [False, CrczpException('destroy failed')],
        ids=['no state', 'destroy failed'],
    )
    def test_resources_are_deleted_by_name_after_a_failed_or_empty_destroy(
        self, cleanup_stage_stack, unit, removal, destroy, destroy_effect
    ):
        """Without a successful destroy, resources go by name and the workspace by force."""
        if isinstance(destroy_effect, Exception):
            destroy.side_effect = destroy_effect
        else:
            destroy.return_value = destroy_effect
        handler = stage_handlers.CleanupStackStageHandler(cleanup_stage_stack)

        handler.execute()

        removal.delete_resources_by_name.assert_called_once()
        assert removal.delete_resources_by_name.call_args.args[:2] == (
            handler._client,
            unit.get_stack_name(),
        )
        removal.delete_workspace.assert_called_once_with(
            handler._client, unit.get_stack_name(), force=True
        )
        assert not SandboxAllocationUnit.objects.filter(pk=unit.pk).exists()

    def test_resources_left_by_name_fail_the_stage_and_keep_the_unit(
        self, cleanup_stage_stack, unit, removal, destroy
    ):
        destroy.return_value = False
        removal.delete_resources_by_name.return_value = ['server web: still there']
        handler = stage_handlers.CleanupStackStageHandler(cleanup_stage_stack)

        with pytest.raises(api_exceptions.StackError, match='server web: still there'):
            handler.execute()

        removal.delete_workspace.assert_not_called()
        assert SandboxAllocationUnit.objects.filter(pk=unit.pk).exists()
        assert Pool.objects.get(pk=unit.pool_id).size == 1

    def test_a_live_state_lock_fails_without_deleting_by_name(
        self, mocker, cleanup_stage_stack, unit, removal, destroy
    ):
        destroy.side_effect = TerraformStateLocked('lock-id')
        mocker.patch.object(
            stage_handlers.CleanupStackStageHandler, '_may_release_state_lock', return_value=False
        )
        handler = stage_handlers.CleanupStackStageHandler(cleanup_stage_stack)
        force_unlock = mocker.patch.object(handler._client, 'force_unlock_stack')

        with pytest.raises(api_exceptions.StackError):
            handler.execute()

        removal.delete_resources_by_name.assert_not_called()
        force_unlock.assert_not_called()
        assert SandboxAllocationUnit.objects.filter(pk=unit.pk).exists()

    def test_a_stale_state_lock_is_released_and_the_destroy_runs_again(
        self, mocker, cleanup_stage_stack, unit, removal, destroy
    ):
        destroy.side_effect = [TerraformStateLocked('lock-id'), True]
        mocker.patch.object(
            stage_handlers.CleanupStackStageHandler, '_may_release_state_lock', return_value=True
        )
        handler = stage_handlers.CleanupStackStageHandler(cleanup_stage_stack)
        force_unlock = mocker.patch.object(handler._client, 'force_unlock_stack')

        handler.execute()

        force_unlock.assert_called_once_with(unit.get_stack_name(), 'lock-id')
        removal.delete_resources_by_name.assert_not_called()

    def test_the_unit_is_removed_after_the_stage_row_is_saved(
        self, mocker, cleanup_stage_stack, unit, removal, destroy
    ):
        saved_before_removal = []
        mocker.patch.object(
            stage_handlers.CleanupStackStageHandler,
            '_finalize',
            side_effect=lambda: saved_before_removal.append(
                StackCleanupStage.objects.get(pk=cleanup_stage_stack.pk).finished
            ),
        )

        stage_handlers.CleanupStackStageHandler(cleanup_stage_stack).execute()

        assert saved_before_removal == [True]

    @pytest.mark.parametrize(
        ('delete_pool_requested', 'pool_deleted'), [(True, True), (False, False)]
    )
    def test_the_pool_is_deleted_with_its_last_unit_only_on_request(
        self,
        cleanup_stage_stack,
        unit,
        removal,
        destroy,
        delete_pool,
        delete_pool_requested,
        pool_deleted,
    ):
        CleanupRequest.objects.filter(pk=cleanup_stage_stack.cleanup_request_id).update(
            delete_pool=delete_pool_requested
        )
        cleanup_stage_stack.cleanup_request.refresh_from_db()

        stage_handlers.CleanupStackStageHandler(cleanup_stage_stack).execute()

        assert delete_pool.called is pool_deleted

    def test_the_pool_is_kept_while_it_holds_other_units(
        self, cleanup_stage_stack, unit, removal, destroy, delete_pool
    ):
        unit.pool.size = 2
        unit.pool.save()
        CleanupRequest.objects.filter(pk=cleanup_stage_stack.cleanup_request_id).update(
            delete_pool=True
        )
        cleanup_stage_stack.cleanup_request.refresh_from_db()

        stage_handlers.CleanupStackStageHandler(cleanup_stage_stack).execute()

        delete_pool.assert_not_called()
        assert Pool.objects.get(pk=unit.pool_id).size == 1

    def test_a_pool_that_cannot_be_deleted_stays_without_failing_the_stage(
        self, now, cleanup_stage_stack, unit, removal, destroy, delete_pool
    ):
        delete_pool.side_effect = api_exceptions.ValidationError('in use')
        CleanupRequest.objects.filter(pk=cleanup_stage_stack.cleanup_request_id).update(
            delete_pool=True
        )
        cleanup_stage_stack.cleanup_request.refresh_from_db()

        stage_handlers.CleanupStackStageHandler(cleanup_stage_stack).execute()

        assert_db_stage(cleanup_stage_stack, now, failed=False)


class TestAllocationAnsibleStageHandler:
    """Tests for the Ansible allocation stage handler."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker, allocation_stage_networking):
        """Set up mocked Docker and Ansible runner for allocation stage tests."""
        mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.LOG')
        mocker.patch.object(
            stage_handlers.AnsibleStageHandler, 'create_directory_path', return_value='directory'
        )
        mocker.patch('crczp.sandbox_instance_app.lib.sandboxes.get_topology_instance')
        mocker.patch('crczp.sandbox_ansible_app.lib.container.DockerContainer._run_container')
        mocker.patch('crczp.sandbox_ansible_app.lib.container.DockerContainer.delete')
        self.generate_role_keypairs = mocker.patch(
            'crczp.sandbox_instance_app.lib.stage_handlers.sandboxes.generate_role_keypairs'
        )

        allocation_runner_class = mocker.patch(
            'crczp.sandbox_instance_app.lib.stage_handlers.AllocationAnsibleRunner'
        )

        mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.AnsibleRunner')

        container = mocker.Mock()
        type(container).id = 'docker-container-id'
        container.logs.return_value = [b'output\n']
        container.wait.return_value = {'StatusCode': 0}
        container_class = DockerContainer(
            'url',
            'rev',
            allocation_stage_networking,
            'container-name',
            'ssh_dir',
            'inventory_path',
            'containers_path',
            'credentials_path',
        )
        container_class.container = container
        container_class.CLIENT = mocker.MagicMock()

        runner = mocker.Mock()
        runner.run_ansible_playbook.return_value = container_class
        allocation_runner_class.return_value = runner

        self.runner = runner
        self.container_class = container_class

    def test_execute_success(self, now, allocation_stage_networking, sandbox):
        """Test successful execution of the Ansible allocation stage."""
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_networking, sandbox)

        handler.execute()

        assert not Container.objects.filter(allocation_stage=allocation_stage_networking).exists()
        assert allocation_stage_networking.outputs.count() == 1
        assert allocation_stage_networking.outputs.first().content == 'output'
        assert_db_stage(allocation_stage_networking, now, failed=False)

    def test_execute_generates_role_keypairs_for_the_networking_stage(
        self, allocation_stage_networking, sandbox
    ):
        """The networking stage generates the sandbox's per-role SSH keypairs."""
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_networking, sandbox)

        handler.execute()

        self.generate_role_keypairs.assert_called_once_with(sandbox, handler.allocation_unit.pool)

    def test_execute_does_not_generate_role_keypairs_for_the_user_stage(
        self, allocation_stage_user, sandbox
    ):
        """The user stage does not regenerate the sandbox's per-role SSH keypairs."""
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_user, sandbox)

        handler.execute()

        self.generate_role_keypairs.assert_not_called()

    def test_the_user_stage_marks_the_sandbox_ready_once_it_succeeded(
        self, allocation_stage_user, sandbox
    ):
        Sandbox.objects.filter(pk=sandbox.pk).update(ready=False)
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_user, sandbox)

        handler.execute()

        allocation_stage_user.refresh_from_db()
        assert allocation_stage_user.finished
        assert Sandbox.objects.get(pk=sandbox.pk).ready

    def test_the_user_build_is_not_counted_as_a_success_when_the_container_cannot_be_deleted(
        self, mocker, allocation_stage_user, sandbox
    ):
        mocker.patch(
            'crczp.sandbox_ansible_app.lib.container.DockerContainer.delete',
            side_effect=CrczpException('cannot delete'),
        )
        Sandbox.objects.filter(pk=sandbox.pk).update(ready=False)
        count_build = mocker.patch.object(AllocationRequest, 'count_build_in_request_group')
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_user, sandbox)

        with pytest.raises(CrczpException):
            handler.execute()

        assert all(
            call.args and isinstance(call.args[0], CrczpException)
            for call in count_build.call_args_list
        )
        assert not Sandbox.objects.get(pk=sandbox.pk).ready

    def test_a_stage_before_the_user_stage_leaves_the_sandbox_not_ready(
        self, allocation_stage_networking, sandbox
    ):
        Sandbox.objects.filter(pk=sandbox.pk).update(ready=False)
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_networking, sandbox)

        handler.execute()

        assert not Sandbox.objects.get(pk=sandbox.pk).ready

    def test_a_failed_user_stage_leaves_the_sandbox_not_ready(self, allocation_stage_user, sandbox):
        Sandbox.objects.filter(pk=sandbox.pk).update(ready=False)
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_user, sandbox)
        self.runner.run_ansible_playbook.side_effect = DockerException('error-message')

        with pytest.raises(DockerException):
            handler.execute()

        assert not Sandbox.objects.get(pk=sandbox.pk).ready

    def test_a_sandbox_deleted_meanwhile_stays_deleted(self, allocation_stage_user, sandbox):
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_user, sandbox)
        Sandbox.objects.filter(pk=sandbox.pk).delete()

        handler.execute()

        assert not Sandbox.objects.filter(pk=sandbox.pk).exists()

    def test_mark_abandoned_fails_the_stage_with_the_reason_of_the_dead_worker(
        self, allocation_stage_networking, sandbox
    ):
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_networking, sandbox)

        handler.mark_abandoned()

        allocation_stage_networking.refresh_from_db()
        assert allocation_stage_networking.failed
        assert allocation_stage_networking.finished
        assert allocation_stage_networking.error_message == stage_handlers.WORKER_DIED_MESSAGE

    def test_execute_failed_to_create_docker(self, now, allocation_stage_networking, sandbox):
        """A Docker creation failure fails the stage and leaves no container record."""
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_networking, sandbox)
        self.runner.run_ansible_playbook.side_effect = DockerException('error-message')

        with pytest.raises(DockerException):
            handler.execute()

        assert not Container.objects.filter(allocation_stage=allocation_stage_networking).exists()
        assert allocation_stage_networking.outputs.count() == 0
        assert_db_stage(allocation_stage_networking, now, failed=True)

    def test_execute_failed_to_obtain_docker_logs(
        self, now, allocation_stage_networking, sandbox, mocker
    ):
        """Test that a failure to get Docker logs marks the stage as failed."""
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_networking, sandbox)
        self.runner.run_ansible_playbook.return_value.get_container_outputs = mocker.MagicMock()
        self.runner.run_ansible_playbook.return_value.get_container_outputs.side_effect = (
            DockerException('error-message')
        )

        with pytest.raises(DockerException):
            handler.execute()

        assert not Container.objects.filter(allocation_stage=allocation_stage_networking).exists()
        assert allocation_stage_networking.outputs.count() == 0
        assert_db_stage(allocation_stage_networking, now, failed=True)

    def test_execute_failed_ansible(self, now, allocation_stage_networking, sandbox):
        """Test that an Ansible failure marks the stage as failed with error output."""
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_networking, sandbox)
        self.runner.run_ansible_playbook.return_value.container.wait.return_value = {
            'StatusCode': -1
        }

        with pytest.raises(api_exceptions.AnsibleError):
            handler.execute()

        assert not Container.objects.filter(allocation_stage=allocation_stage_networking).exists()
        assert allocation_stage_networking.outputs.count() == 1
        assert allocation_stage_networking.outputs.first().content == 'output'
        assert_db_stage(allocation_stage_networking, now, failed=True)

    @pytest.mark.usefixtures('fake_redis')
    def test_cancel_deletes_container(self, mocker, allocation_stage_networking_started, sandbox):
        """Cancelling a started Ansible allocation stage deletes its container and fails it."""
        runner = mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.AnsibleRunner')
        handler = stage_handlers.AllocationAnsibleStageHandler(
            allocation_stage_networking_started, sandbox
        )

        handler.request_stop()
        handler.mark_cancelled()

        runner.return_value.delete_container.assert_called_once_with('docker-container-id')
        stage = NetworkingAnsibleAllocationStage.objects.get(
            pk=allocation_stage_networking_started.id
        )
        with pytest.raises(ObjectDoesNotExist):
            assert stage.container
        assert_db_cancelled(stage)

    @pytest.mark.usefixtures('fake_redis')
    def test_cancel_nonexistent_docker_container(
        self, mocker, allocation_stage_networking_started, sandbox
    ):
        """Cancelling a stage whose Docker container is gone still fails the stage."""
        runner = mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.AnsibleRunner')
        runner.return_value.delete_container.side_effect = DockerContainerNotFound('error-message')
        handler = stage_handlers.AllocationAnsibleStageHandler(
            allocation_stage_networking_started, sandbox
        )

        handler.request_stop()
        handler.mark_cancelled()

        assert_db_cancelled(allocation_stage_networking_started)

    def test_execute_records_the_container_before_starting_it(
        self, allocation_stage_networking, sandbox
    ):
        """The container record exists, under the name the container gets, before it starts."""
        names_recorded_at_start = []

        def start_container(_url: str, _rev: str, stage: Any, _container_name: str) -> Any:
            names_recorded_at_start.extend(
                Container.objects.filter(allocation_stage=stage).values_list(
                    'container_name', flat=True
                )
            )
            return self.container_class

        self.runner.run_ansible_playbook.side_effect = start_container
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_networking, sandbox)

        handler.execute()

        assert names_recorded_at_start == [self.runner.run_ansible_playbook.call_args.args[3]]
        assert names_recorded_at_start[0].startswith(
            f'ansible-allocation-{allocation_stage_networking.id}-'
        )

    def test_a_failed_container_start_leaves_no_container_record(
        self, allocation_stage_networking, sandbox
    ):
        """A container that never started leaves no record behind."""
        self.runner.run_ansible_playbook.side_effect = CrczpException('cannot start')
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_networking, sandbox)

        with pytest.raises(CrczpException):
            handler.execute()

        assert not Container.objects.filter(allocation_stage=allocation_stage_networking).exists()

    @pytest.mark.parametrize(
        'runtime_error',
        [DockerException('daemon down'), requests.exceptions.ConnectionError('socket closed')],
    )
    def test_cancel_survives_a_container_runtime_error(
        self, mocker, allocation_stage_networking_started, sandbox, runtime_error
    ):
        """A container runtime that cannot be reached keeps the record and raises nothing."""
        runner = mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.AnsibleRunner')
        runner.return_value.delete_container.side_effect = runtime_error
        handler = stage_handlers.AllocationAnsibleStageHandler(
            allocation_stage_networking_started, sandbox
        )

        handler._cancel()  # pylint: disable=protected-access

        assert Container.objects.filter(
            allocation_stage=allocation_stage_networking_started
        ).exists()

    @pytest.mark.parametrize(
        'not_found', [DockerContainerNotFound('not started'), CrczpException('not started')]
    )
    @pytest.mark.usefixtures('fake_redis')
    def test_cancel_deletes_a_container_started_after_the_stop(
        self, mocker, allocation_stage_networking_started, sandbox, not_found
    ):
        """A container missing at the stop keeps its record and is deleted once cancelled."""
        runner = mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.AnsibleRunner')
        runner.return_value.delete_container.side_effect = [not_found, None]
        handler = stage_handlers.AllocationAnsibleStageHandler(
            allocation_stage_networking_started, sandbox
        )

        handler.request_stop()
        assert Container.objects.filter(
            allocation_stage=allocation_stage_networking_started
        ).exists()

        handler.mark_cancelled()

        assert runner.return_value.delete_container.call_count == 2
        assert not Container.objects.filter(
            allocation_stage=allocation_stage_networking_started
        ).exists()
        assert_db_cancelled(allocation_stage_networking_started)


class TestCleanupAnsibleStageHandler:
    """Tests for the Ansible cleanup stage handler."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker):
        """Silence stage handler logging for cleanup stage tests."""
        mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.LOG')

    @pytest.mark.usefixtures('fake_redis')
    def test_cancel_success(self, cleanup_stage_networking_started):
        """Cancelling a started Ansible cleanup stage fails it."""
        handler = stage_handlers.CleanupAnsibleStageHandler(cleanup_stage_networking_started)

        handler.request_stop()
        handler.mark_cancelled()

        assert_db_cancelled(cleanup_stage_networking_started)

    def test_networking_stage_tears_down_netbird_then_the_jump_key(
        self, mocker, cleanup_stage_networking
    ):
        """The networking cleanup removes the unit's NetBird resources and its jump host key."""
        teardown = mocker.Mock()
        teardown.attach_mock(
            mocker.patch.object(
                stage_handlers.netbird, 'destroy_netbird_for_unit', return_value=[]
            ),
            'netbird',
        )
        teardown.attach_mock(mocker.patch.object(stage_handlers, 'delete_jump_ssh_key'), 'jump_key')
        unit = cleanup_stage_networking.cleanup_request_fk_many.allocation_unit
        handler = stage_handlers.CleanupAnsibleStageHandler(cleanup_stage_networking)

        handler._execute()  # pylint: disable=protected-access

        assert teardown.mock_calls == [call.netbird(unit), call.jump_key(unit)]

    def test_a_failed_netbird_teardown_fails_the_networking_stage(
        self, mocker, cleanup_stage_networking
    ):
        """NetBird objects left behind fail the stage, after the jump host key is removed."""
        mocker.patch.object(
            stage_handlers.netbird,
            'destroy_netbird_for_unit',
            return_value=['policy pol-1: HTTP 500'],
        )
        delete_key = mocker.patch.object(stage_handlers, 'delete_jump_ssh_key')
        handler = stage_handlers.CleanupAnsibleStageHandler(cleanup_stage_networking)

        with pytest.raises(api_exceptions.NetworkError, match='policy pol-1: HTTP 500'):
            handler._execute()  # pylint: disable=protected-access

        delete_key.assert_called_once()

    def test_a_failed_jump_host_teardown_fails_the_networking_stage(
        self, mocker, cleanup_stage_networking
    ):
        """A jump host home that cannot be removed fails the stage."""
        mocker.patch.object(stage_handlers.netbird, 'destroy_netbird_for_unit', return_value=[])
        mocker.patch.object(
            stage_handlers,
            'delete_jump_ssh_key',
            side_effect=api_exceptions.NetworkError('rm: cannot remove'),
        )
        handler = stage_handlers.CleanupAnsibleStageHandler(cleanup_stage_networking)

        with pytest.raises(api_exceptions.NetworkError, match='rm: cannot remove'):
            handler._execute()  # pylint: disable=protected-access

    def test_user_stage_leaves_netbird_alone(self, mocker, cleanup_stage_user):
        """The user cleanup stage tears nothing down."""
        destroy = mocker.patch.object(stage_handlers.netbird, 'destroy_netbird_for_unit')
        handler = stage_handlers.CleanupAnsibleStageHandler(cleanup_stage_user)

        handler._execute()  # pylint: disable=protected-access

        destroy.assert_not_called()


class TestStackOutputLineBreaks:
    """Tests for the line breaks of the Terraform output rows a stack handler stores."""

    def test_log_process_output_stores_line_feeds_only(
        self, mocker, allocation_stage_stack, process
    ):
        """A carriage return inside a Terraform output line is stored as a line feed."""
        client = mocker.Mock()
        client.get_process_output.return_value = ['plan 1%\rplan 2%\r\n']
        handler = stage_handlers.AllocationStackStageHandler(allocation_stage_stack)
        mocker.patch.object(handler, '_client', client)

        handler._log_process_output(  # pylint: disable=protected-access
            process, AllocationTerraformOutput, allocation_stage=allocation_stage_stack
        )

        assert list(allocation_stage_stack.terraform_outputs.values_list('content', flat=True)) == [
            'plan 1%\nplan 2%'
        ]


class TestNetbirdProvisioningHandlerStop:
    """Tests for stopping the NetBird provisioning job of an allocation request."""

    @pytest.mark.parametrize(
        ('status', 'dependents_deleted'),
        [(JobStatus.FINISHED, False), (JobStatus.DEFERRED, True)],
    )
    def test_the_dependents_of_a_finished_job_are_kept(
        self, mocker, allocation_request, status, dependents_deleted
    ):
        job = mocker.Mock()
        job.get_status.return_value = status
        mocker.patch.object(stage_handlers.Job, 'fetch', return_value=job)
        allocation_request.netbird_job_id = 'netbird'
        handler = stage_handlers.NetbirdProvisioningHandler(allocation_request)

        handler.request_stop()

        job.delete.assert_called_once_with(delete_dependents=dependents_deleted)


class TestFailUnfinishedAtomicity:
    """The failure mark is one guarded statement on the table that holds the stage state."""

    @pytest.mark.parametrize('unless_stop_requested', [False, True])
    def test_the_guards_are_part_of_the_update_statement(
        self, mocker, allocation_stage_networking_started, sandbox, unless_stop_requested
    ):
        mocker.patch('crczp.sandbox_instance_app.lib.stage_handlers.AnsibleRunner')
        handler = stage_handlers.AllocationAnsibleStageHandler(
            allocation_stage_networking_started, sandbox
        )
        unfinished_guard = 'NOT "sandbox_instance_app_allocationstage"."finished"'

        with CaptureQueriesContext(connection) as captured:
            marked = handler.mark_abandoned(unless_stop_requested=unless_stop_requested)

        assert marked
        statements = [query['sql'] for query in captured.captured_queries]
        failure_updates = [
            sql for sql in statements if sql.startswith('UPDATE') and '"error_message"' in sql
        ]
        assert len(failure_updates) == 1
        assert unfinished_guard in failure_updates[0]
        assert ('"stop_requested_at" IS NULL' in failure_updates[0]) is unless_stop_requested
        assert not any(sql.startswith('SELECT') and unfinished_guard in sql for sql in statements)
