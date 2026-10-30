"""Tests for sandbox instance request handlers."""

# pylint: disable=missing-function-docstring
from datetime import timedelta
from typing import Any, override
from unittest.mock import MagicMock, call

import pytest
from django.conf import settings
from django_rq import get_queue, get_worker

from crczp.sandbox_ansible_app.models import (
    NetworkingAnsibleAllocationStage,
    NetworkingAnsibleCleanupStage,
    UserAnsibleAllocationStage,
    UserAnsibleCleanupStage,
)
from crczp.sandbox_common_lib import exceptions as api_exceptions
from crczp.sandbox_instance_app.lib import request_handlers, stage_handlers
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    AllocationRQJob,
    AllocationStage,
    CleanupRequest,
    CleanupRQJob,
    Pool,
    Sandbox,
    SandboxAllocationUnit,
    SandboxRequestGroup,
    StackAllocationStage,
    StackCleanupStage,
)

pytestmark = [pytest.mark.django_db]


def _make_picklable_mock() -> 'PicklableMock':
    """Module-level factory used by PicklableMock.__reduce__ for deserialization."""
    return PicklableMock()


class PicklableMock(MagicMock):
    """MagicMock subclass that can be pickled for use with RQ workers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Return None so RQ can pickle the job result without error.
        self.return_value = None

    @override
    def __reduce__(self):
        """Return pickling instructions that reconstruct as PicklableMock(return_value=None)."""
        return (_make_picklable_mock, ())


def empty_queues() -> None:
    for queue_name in ['default', 'openstack', 'ansible']:
        queue = get_queue(queue_name)
        queue.empty()
        for register in [
            queue.started_job_registry,
            queue.deferred_job_registry,
            queue.finished_job_registry,
            queue.failed_job_registry,
            queue.scheduled_job_registry,
        ]:
            for job_id in register.get_job_ids():
                register.remove(job_id)


class TestAllocationRequestHandlerUnit:
    """Unit tests for AllocationRequestHandler."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker):
        mocker.patch('crczp.sandbox_instance_app.lib.request_handlers.LOG')
        mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.sandboxes.generate_new_sandbox_uuid',
            return_value='123',
        )
        fake_crczp_config = MagicMock(
            ansible_networking_url='fake_repo_url', ansible_networking_rev='fake_rev'
        )
        mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.settings.CRCZP_CONFIG',
            new=fake_crczp_config,
        )
        # CRCZP_CONFIG is fully mocked above, so the netbird config looks
        # "present"; neutralise the external provisioning side-effect.
        mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.netbird.provision_netbird_for_sandbox'
        )
        self.handler = request_handlers.AllocationRequestHandler()
        self.fake_gen_ssh = mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.utils.generate_ssh_keypair'
        )
        self.fake_gen_ssh.return_value = ('fake_private_key', 'fake_public_key')

    @staticmethod
    def assert_handlers(handlers: Any, stages: Any) -> None:
        stack_handler = handlers[0]
        assert stack_handler.stage == stages[0]
        network_handler = handlers[1]
        assert network_handler.stage == stages[1]
        user_handler = handlers[2]
        assert user_handler.stage == stages[2]

    def test_enqueue_stages(self, sandbox, mocker):
        fake_transaction = mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.transaction'
        )
        fake_partial = mocker.patch('crczp.sandbox_instance_app.lib.request_handlers.partial')
        fake_partial.return_value = 'fake_partial'

        # The sentinel string stands in for the stage-handler list: the test asserts only
        # that _enqueue_stages forwards it verbatim to functools.partial.
        self.handler._enqueue_stages(sandbox, 'stage_handlers')  # ty: ignore[invalid-argument-type]
        fake_partial.assert_called_once_with(
            self.handler._enqueue_allocation_request,
            sandbox,
            'stage_handlers',
            self.handler.request,
        )
        fake_transaction.on_commit.assert_called_once_with(
            'fake_partial'
        )  # partial messes with assert_called_once_with

    @pytest.mark.parametrize('configured', [True, False])
    def test_enqueue_stores_the_stage_and_netbird_job_ids_before_any_job_is_queued(
        self, mocker, sandbox, allocation_request, configured
    ):
        allocation_request.netbird_job_id = 'previous-job'
        allocation_request.save()
        mocker.patch.object(
            request_handlers.netbird, 'is_netbird_configured', return_value=configured
        )
        stage_handler = MagicMock()
        stored_before_queued: list[tuple[bool, str | None]] = []

        def record_stored_state(*_args: Any, **_kwargs: Any) -> None:
            allocation_request.refresh_from_db()
            stored_before_queued.append((
                stage_handler.set_job_id.called,
                allocation_request.netbird_job_id,
            ))

        queue = mocker.patch.object(self.handler, 'queue_default')
        queue.enqueue.side_effect = record_stored_state
        mocker.patch.object(self.handler, 'queue_ansible').enqueue.side_effect = record_stored_state

        self.handler._enqueue_allocation_request(sandbox, [stage_handler], allocation_request)

        allocation_request.refresh_from_db()
        assert all(stage_stored for stage_stored, _ in stored_before_queued)
        assert {netbird_id for _, netbird_id in stored_before_queued} == {
            allocation_request.netbird_job_id
        }
        assert (allocation_request.netbird_job_id is not None) == configured
        assert len(stored_before_queued) == 1 + configured
        (stored_stage_job_id,) = stage_handler.set_job_id.call_args.args
        stage_enqueue = queue.enqueue.call_args
        assert stage_enqueue.kwargs['job_id'] == stored_stage_job_id
        assert stage_enqueue.kwargs['depends_on'] is None

    def test_a_unit_removed_before_the_job_ran_is_not_counted_in_the_request_group(self, pool):
        """The group counts only the units that exist when the job creates it."""
        self.handler._enqueue_stages = MagicMock()
        self.handler._create_stage_handlers = MagicMock()
        pool.send_emails = True
        removed = SandboxAllocationUnit.objects.create(pool=pool)
        kept = SandboxAllocationUnit.objects.create(pool=pool)
        SandboxAllocationUnit.objects.filter(pk=removed.pk).delete()
        user = MagicMock(email='a@b.c')

        self.handler._create_allocation_jobs([removed, kept], user)

        group = AllocationRequest.objects.get(allocation_unit=kept).request_group
        assert group is not None
        assert group.unit_count == 1

    def test_a_request_group_without_units_left_is_deleted(self, pool):
        group = SandboxRequestGroup.objects.create(pool=pool, unit_count=1, email='a@b.c')

        group.on_allocation_removed()

        assert not SandboxRequestGroup.objects.filter(pk=group.pk).exists()

    def test_create_allocation_jobs_links_each_request_to_the_group(self, pool, mocker):
        """Every allocation request built for the group links to it."""
        self.handler._enqueue_stages = MagicMock()
        self.handler._create_stage_handlers = MagicMock()
        mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.Sandbox', return_value=MagicMock()
        )
        pool.send_emails = True
        group = SandboxRequestGroup.objects.create(pool=pool, unit_count=2, email='a@b.c')
        mocker.patch.object(self.handler, 'create_request_group', return_value=group)
        units = [SandboxAllocationUnit.objects.create(pool=pool) for _ in range(2)]

        self.handler._create_allocation_jobs(units, MagicMock(email='a@b.c'))

        assert [
            AllocationRequest.objects.get(allocation_unit=unit).request_group for unit in units
        ] == [group, group]

    def test_create_allocation_jobs(self, pool, mocker):
        self.handler._enqueue_stages = MagicMock()
        self.handler._create_stage_handlers = MagicMock()
        fake_sandbox = MagicMock()
        fake_sandbox_class = mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.Sandbox', return_value=fake_sandbox
        )

        units = [SandboxAllocationUnit.objects.create(pool=pool) for _ in range(2)]

        self.handler._create_allocation_jobs(units, None)

        for unit in units:
            assert hasattr(unit, 'allocation_request')
            assert not hasattr(unit, 'sandbox')
            fake_sandbox_class.assert_has_calls([
                call(
                    id='123',
                    allocation_unit=unit,
                    private_user_key='fake_private_key',
                    public_user_key='fake_public_key',
                )
            ])
            self.handler._enqueue_stages.assert_has_calls([
                call(fake_sandbox, self.handler._create_stage_handlers.return_value)
            ])

    def test_create_allocation_jobs_skips_removed_units(self, pool, mocker):
        """A unit Cancel all allocations removed while the job waited is skipped, not built."""
        self.handler._enqueue_stages = MagicMock()
        self.handler._create_stage_handlers = MagicMock()
        fake_sandbox_class = mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.Sandbox', return_value=MagicMock()
        )
        removed, kept = (SandboxAllocationUnit.objects.create(pool=pool) for _ in range(2))
        SandboxAllocationUnit.objects.filter(pk=removed.pk).delete()

        self.handler._create_allocation_jobs([removed, kept], None)

        fake_sandbox_class.assert_called_once()
        assert fake_sandbox_class.call_args.kwargs['allocation_unit'] == kept
        assert not AllocationRequest.objects.filter(allocation_unit_id=removed.pk).exists()
        self.handler._enqueue_stages.assert_called_once()

    def test_enqueue_request(self, allocation_unit, created_by):
        self.handler.queue_default.enqueue = MagicMock()
        self.handler.enqueue_request(allocation_unit, created_by)
        self.handler.queue_default.enqueue.assert_called_once_with(
            self.handler._create_allocation_jobs, allocation_unit, created_by
        )

    def test_create_restart_jobs(self, allocation_unit, allocation_request, mocker):
        fake_sandbox = MagicMock()
        fake_sandbox_class = mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.Sandbox', return_value=fake_sandbox
        )
        fake_destroy = mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.netbird.destroy_netbird_for_unit',
            return_value=[],
        )
        self.handler._ensure_restartable = MagicMock()
        self.handler._restart_stage_handlers = MagicMock()
        self.handler._enqueue_stages = MagicMock()

        self.handler._create_restart_jobs(allocation_unit)

        assert self.handler.request == allocation_request
        # The unit's NetBird resources are torn down before the old sandbox is deleted.
        old_sandbox = fake_sandbox_class.objects.get.return_value
        fake_destroy.assert_called_once_with(allocation_unit)
        old_sandbox.delete.assert_called_once()
        fake_sandbox_class.assert_called_once_with(
            id='123',
            allocation_unit=allocation_unit,
            private_user_key='fake_private_key',
            public_user_key='fake_public_key',
        )
        self.handler._restart_stage_handlers.assert_called_once_with(fake_sandbox)
        self.handler._enqueue_stages.assert_called_once_with(
            fake_sandbox, self.handler._restart_stage_handlers.return_value
        )

    def test_a_refused_restart_destroys_nothing(self, sandbox_finished, mocker):
        unit = sandbox_finished.allocation_unit
        destroy = mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.netbird.destroy_netbird_for_unit'
        )

        with pytest.raises(api_exceptions.ValidationError):
            self.handler._create_restart_jobs(unit)

        destroy.assert_not_called()
        assert Sandbox.objects.filter(pk=sandbox_finished.pk).exists()

    def test_restart_request(self, allocation_request):
        self.handler.queue_default.enqueue = MagicMock()
        self.handler.restart_request(allocation_request.allocation_unit)
        self.handler.queue_default.enqueue.assert_called_once_with(
            self.handler._create_restart_jobs, allocation_request.allocation_unit
        )

    def test_create_db_stage(self, allocation_request):
        self.handler.request = allocation_request
        self.handler._create_db_stage(
            NetworkingAnsibleAllocationStage, repo_url='fake_repo_url', rev='fake_rev'
        )

        assert allocation_request.stages.count() == 1
        stage = NetworkingAnsibleAllocationStage.objects.get(allocation_request=allocation_request)
        assert stage.repo_url == 'fake_repo_url'
        assert stage.rev == 'fake_rev'

    def test_create_stage_handlers(self, sandbox, allocation_request, mocker):
        self.handler.request = allocation_request

        fake_stack_stage = MagicMock()
        fake_network_stage = MagicMock()
        fake_user_stage = MagicMock()
        self.handler._create_db_stage = MagicMock()
        self.handler._create_db_stage.side_effect = [
            fake_stack_stage,
            fake_network_stage,
            fake_user_stage,
        ]

        handlers = self.handler._create_stage_handlers(sandbox)

        self.handler._create_db_stage.assert_has_calls([
            call(StackAllocationStage),
            call(NetworkingAnsibleAllocationStage, repo_url='fake_repo_url', rev='fake_rev'),
            call(
                UserAnsibleAllocationStage,
                repo_url=allocation_request.allocation_unit.pool.definition.url,
                rev=allocation_request.allocation_unit.pool.rev_sha,
            ),
        ])
        self.assert_handlers(handlers, [fake_stack_stage, fake_network_stage, fake_user_stage])

    def test_restart_stage_handlers_not_finished(self, allocation_request_started, sandbox):
        self.handler.request = allocation_request_started
        with pytest.raises(api_exceptions.ValidationError) as cm:
            self.handler._restart_stage_handlers(sandbox)

        assert str(cm.value) == 'Allocation of the sandbox is still in progress.'

    def test_restart_stage_handlers_finished_successfully(self, sandbox_finished):
        self.handler.request = sandbox_finished.allocation_unit.allocation_request
        with pytest.raises(api_exceptions.ValidationError) as cm:
            self.handler._restart_stage_handlers(sandbox_finished)

        assert (
            str(cm.value)
            == 'All stages finished without failing. Only failed stages can be restarted.'
        )

    def test_restart_stage_handlers(self, sandbox_failed_stack_stage):
        self.handler.request = sandbox_failed_stack_stage.allocation_unit.allocation_request

        fake_stack_stage = MagicMock()
        fake_network_stage = MagicMock()
        fake_user_stage = MagicMock()
        self.handler._create_db_stage = MagicMock()
        self.handler._create_db_stage.side_effect = [
            fake_stack_stage,
            fake_network_stage,
            fake_user_stage,
        ]

        handlers = self.handler._restart_stage_handlers(sandbox_failed_stack_stage)

        self.handler._create_db_stage.assert_has_calls([
            call(StackAllocationStage),
            call(NetworkingAnsibleAllocationStage, repo_url='fake_repo_url', rev='fake_rev'),
            call(
                UserAnsibleAllocationStage,
                repo_url=sandbox_failed_stack_stage.allocation_unit.pool.definition.url,
                rev=sandbox_failed_stack_stage.allocation_unit.pool.rev_sha,
            ),
        ])
        self.assert_handlers(handlers, [fake_stack_stage, fake_network_stage, fake_user_stage])

    def test_restart_stage_handler_user_stage_failed(self, sandbox_failed_user_stage):
        self.handler.request = sandbox_failed_user_stage.allocation_unit.allocation_request

        fake_user_stage = MagicMock()
        self.handler._create_db_stage = MagicMock(return_value=fake_user_stage)

        handlers = self.handler._restart_stage_handlers(sandbox_failed_user_stage)

        self.handler._create_db_stage.assert_called_once_with(
            UserAnsibleAllocationStage,
            repo_url=sandbox_failed_user_stage.allocation_unit.pool.definition.url,
            rev=sandbox_failed_user_stage.allocation_unit.pool.rev_sha,
        )
        user_stage = handlers[0]
        assert user_stage.stage == fake_user_stage

    def test_get_stage_handlers(self, allocation_request_started):
        self.handler.request = allocation_request_started

        handlers = self.handler._get_stage_handlers()

        self.assert_handlers(
            handlers,
            [
                allocation_request_started.useransibleallocationstage,
                allocation_request_started.networkingansibleallocationstage,
                allocation_request_started.stackallocationstage,
            ],
        )


class TestCleanupRequestHandlerUnit:
    """Unit tests for CleanupRequestHandler."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker):
        mocker.patch('crczp.sandbox_instance_app.lib.request_handlers.LOG')
        self.handler = request_handlers.CleanupRequestHandler()

    @staticmethod
    def assert_handlers(handlers: Any, stages: Any) -> None:
        assert len(handlers) == len(stages)
        for handler, stage in zip(handlers, stages, strict=False):
            assert handler.stage == stage

    def test_enqueue_stages(self, mocker, allocation_request):
        self.handler.request = allocation_request
        self.handler._create_stage_handlers = MagicMock(return_value='fake_handlers')
        fake_partial = mocker.patch('crczp.sandbox_instance_app.lib.request_handlers.partial')
        fake_transaction = mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.transaction'
        )

        self.handler._enqueue_stages()

        self.handler._create_stage_handlers.assert_called_once()
        fake_partial.assert_called_once_with(self.handler._enqueue_cleanup_request, 'fake_handlers')
        fake_transaction.on_commit.assert_called_once_with(fake_partial.return_value)

    def test_enqueue_stores_the_stage_job_ids_before_queueing_them(self, mocker):
        stage_handler = MagicMock()
        queue = mocker.patch.object(self.handler, 'queue_default')
        queue.enqueue.side_effect = lambda *_args, **_kwargs: (
            stage_handler.set_job_id.assert_called_once()
        )

        self.handler._enqueue_cleanup_request([stage_handler])

        (stored_job_id,) = stage_handler.set_job_id.call_args.args
        assert queue.enqueue.call_args.kwargs['job_id'] == stored_job_id

    def test_enqueue_request_stores_whether_the_pool_is_deleted(self, allocation_unit):
        request_handlers.CleanupRequestHandler(delete_pool=True).enqueue_request(allocation_unit)

        assert CleanupRequest.objects.get(allocation_unit=allocation_unit).delete_pool

    def test_enqueue_request_restarts_an_existing_request(self, cleanup_request):
        cleanup_request.retry_count = 2
        cleanup_request.save()

        self.handler.enqueue_request(cleanup_request.allocation_unit)

        cleanup_request.refresh_from_db()
        assert cleanup_request.retry_count == 0
        assert not cleanup_request.delete_pool

    @pytest.fixture
    def failed_cleanup(self, cleanup_request, cleanup_stage_stack):
        cleanup_stage_stack.finished = True
        cleanup_stage_stack.failed = True
        cleanup_stage_stack.save()
        return cleanup_request

    def test_a_failed_cleanup_is_retried_with_a_delay(self, mocker, failed_cleanup):
        mocker.patch.object(settings.CRCZP_CONFIG, 'cleanup_retry_limit', 3)
        mocker.patch.object(settings.CRCZP_CONFIG, 'cleanup_retry_delay', 60)
        self.handler.queue_default.enqueue_in = MagicMock()
        self.handler.queue_default.enqueue = MagicMock()

        assert self.handler.retry_request(failed_cleanup)

        unit_id = failed_cleanup.allocation_unit.pk
        self.handler.queue_default.enqueue_in.assert_called_once_with(
            timedelta(seconds=60), self.handler._rerun_failed_cleanup, unit_id, 1
        )
        self.handler.queue_default.enqueue.assert_not_called()
        failed_cleanup.refresh_from_db()
        assert failed_cleanup.retry_count == 1

    def test_a_failed_cleanup_is_retried_at_once_without_a_delay(self, mocker, failed_cleanup):
        mocker.patch.object(settings.CRCZP_CONFIG, 'cleanup_retry_limit', 3)
        mocker.patch.object(settings.CRCZP_CONFIG, 'cleanup_retry_delay', 0)
        self.handler.queue_default.enqueue_in = MagicMock()
        self.handler.queue_default.enqueue = MagicMock()

        assert self.handler.retry_request(failed_cleanup)

        self.handler.queue_default.enqueue.assert_called_once_with(
            self.handler._rerun_failed_cleanup, failed_cleanup.allocation_unit.pk, 1
        )
        self.handler.queue_default.enqueue_in.assert_not_called()

    def test_a_cleanup_stays_failed_at_the_retry_limit(self, mocker, failed_cleanup):
        mocker.patch.object(settings.CRCZP_CONFIG, 'cleanup_retry_limit', 2)
        failed_cleanup.retry_count = 2
        failed_cleanup.save()
        self.handler.queue_default.enqueue_in = MagicMock()
        self.handler.queue_default.enqueue = MagicMock()

        assert not self.handler.retry_request(failed_cleanup)

        self.handler.queue_default.enqueue_in.assert_not_called()
        self.handler.queue_default.enqueue.assert_not_called()
        failed_cleanup.refresh_from_db()
        assert failed_cleanup.retry_count == 2

    def test_a_retry_recreates_the_stages_of_a_failed_cleanup(self, failed_cleanup):
        failed_cleanup.retry_count = 1
        failed_cleanup.save()
        self.handler._create_cleanup_jobs = MagicMock()

        self.handler._rerun_failed_cleanup(failed_cleanup.allocation_unit.pk, 1)

        self.handler._create_cleanup_jobs.assert_called_once_with(failed_cleanup.allocation_unit)

    def test_a_retry_of_a_cleanup_started_anew_is_skipped(self, failed_cleanup):
        failed_cleanup.retry_count = 0
        failed_cleanup.save()
        self.handler._create_cleanup_jobs = MagicMock()

        self.handler._rerun_failed_cleanup(failed_cleanup.allocation_unit.pk, 1)

        self.handler._create_cleanup_jobs.assert_not_called()

    def test_a_retry_of_a_removed_cleanup_is_skipped(self, allocation_unit):
        self.handler._create_cleanup_jobs = MagicMock()

        self.handler._rerun_failed_cleanup(allocation_unit.pk, 1)

        self.handler._create_cleanup_jobs.assert_not_called()

    def test_a_retry_of_an_unfinished_cleanup_is_skipped(self, cleanup_request_started):
        cleanup_request_started.retry_count = 1
        cleanup_request_started.save()
        self.handler._create_cleanup_jobs = MagicMock()

        self.handler._rerun_failed_cleanup(cleanup_request_started.allocation_unit.pk, 1)

        self.handler._create_cleanup_jobs.assert_not_called()

    def test_create_cleanup_jobs(self, cleanup_request):
        self.handler.request = cleanup_request
        self.handler._enqueue_stages = MagicMock()

        self.handler._create_cleanup_jobs(cleanup_request.allocation_unit)

        self.handler._enqueue_stages.assert_called_once()
        assert self.handler.request == cleanup_request

    def test_create_cleanup_jobs_no_request(self, allocation_unit):
        self.handler._enqueue_stages = MagicMock()

        self.handler._create_cleanup_jobs(allocation_unit)

        self.handler._enqueue_stages.assert_called_once()
        assert self.handler.request == allocation_unit.cleanup_request

    def test_create_db_stage(self, cleanup_request):
        self.handler.request = cleanup_request
        self.handler._create_db_stage(NetworkingAnsibleCleanupStage)

        assert self.handler.request.networkingansiblecleanupstage is not None

    def test_create_stage_handlers(self, cleanup_request_finished):
        self.handler.request = cleanup_request_finished
        fake_user_stage = MagicMock()
        fake_network_stage = MagicMock()
        fake_stack_stage = MagicMock()
        self.handler._create_db_stage = MagicMock(
            side_effect=[fake_user_stage, fake_network_stage, fake_stack_stage]
        )

        handlers = self.handler._create_stage_handlers()

        self.handler._create_db_stage.assert_has_calls([
            call(UserAnsibleCleanupStage),
            call(NetworkingAnsibleCleanupStage),
            call(StackCleanupStage),
        ])
        self.assert_handlers(handlers, [fake_user_stage, fake_network_stage, fake_stack_stage])

    def test_create_stage_handlers_no_user_stage(self, cleanup_stage_networking_started):
        self.handler.request = cleanup_stage_networking_started.cleanup_request
        StackCleanupStage.delete = MagicMock()
        UserAnsibleCleanupStage.delete = MagicMock()
        self.handler._create_db_stage = MagicMock(
            side_effect=[MagicMock(), MagicMock(), MagicMock()]
        )

        self.handler._create_stage_handlers()

        self.handler._create_db_stage.assert_has_calls([
            call(UserAnsibleCleanupStage),
            call(NetworkingAnsibleCleanupStage),
            call(StackCleanupStage),
        ])
        UserAnsibleCleanupStage.delete.assert_called_once()
        StackCleanupStage.delete.assert_not_called()

    def test_get_stage_handlers(self, cleanup_request_finished):
        self.handler.request = cleanup_request_finished

        handlers = self.handler._get_stage_handlers()

        self.assert_handlers(
            handlers,
            [
                cleanup_request_finished.stackcleanupstage,
                cleanup_request_finished.networkingansiblecleanupstage,
                cleanup_request_finished.useransiblecleanupstage,
            ],
        )


@pytest.mark.integration
class TestAllocationRequestHandler:
    """Integration tests for AllocationRequestHandler."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker):
        empty_queues()

        stack_execute_patch_target = (
            'crczp.sandbox_instance_app.lib.request_handlers.AllocationStackStageHandler.execute'
        )
        mocker.patch(stack_execute_patch_target, new_callable=PicklableMock)
        self.stack_stop = mocker.patch.object(
            request_handlers.AllocationStackStageHandler,
            'request_stop',
            new_callable=PicklableMock,
        )

        ansible_execute_patch_target = (
            'crczp.sandbox_instance_app.lib.request_handlers.AllocationAnsibleStageHandler.execute'
        )
        mocker.patch(ansible_execute_patch_target, new_callable=PicklableMock)
        self.ansible_stop = mocker.patch.object(
            request_handlers.AllocationAnsibleStageHandler,
            'request_stop',
            new_callable=PicklableMock,
        )

    @pytest.mark.django_db(transaction=True)
    def test_enqueue_request(self, allocation_unit):
        handler = request_handlers.AllocationRequestHandler()

        handler.enqueue_request([allocation_unit], None)

        worker = get_worker('openstack', 'ansible', 'default')
        worker.work(burst=True)

        allocation_request = AllocationRequest.objects.get(allocation_unit=allocation_unit)
        assert allocation_request.stages.all().count() == 3
        assert isinstance(allocation_request.stackallocationstage, StackAllocationStage)
        assert isinstance(
            allocation_request.networkingansibleallocationstage, NetworkingAnsibleAllocationStage
        )
        assert isinstance(allocation_request.useransibleallocationstage, UserAnsibleAllocationStage)

        job_stack = handler.queue_stack.fetch_job(
            allocation_request.stackallocationstage.rq_job.job_id
        )
        job_networking = handler.queue_ansible.fetch_job(
            allocation_request.networkingansibleallocationstage.rq_job.job_id
        )
        job_user = handler.queue_ansible.fetch_job(
            allocation_request.useransibleallocationstage.rq_job.job_id
        )

        assert job_stack is not None
        assert job_networking is not None
        assert job_user is not None
        assert job_stack.is_finished
        assert job_networking.is_finished
        assert job_user.is_finished

    @pytest.mark.django_db(transaction=True)
    def test_netbird_provisioning_does_not_starve_other_pools(
        self, pool, definition, created_by, mocker
    ):
        """Allocation orchestration no longer blocks on NetBird.

        NetBird provisioning is enqueued as a SEPARATE job on the ansible queue,
        so running only the ``default``-queue worker creates BOTH pools'
        AllocationRequests (no cross-pool starvation) while provisioning waits on
        its own queue. The user-ansible stage depends on the provisioning job.
        """
        mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.netbird.is_netbird_configured',
            return_value=True,
        )
        pool_b = Pool.objects.create(
            definition=definition,
            max_size=3,
            private_management_key='-----RSA PRIVATE KEY-----',
            public_management_key='ssh-rsa',
            uuid='0fb3160e',
            created_by=created_by,
        )
        unit_a = SandboxAllocationUnit.objects.create(pool=pool, created_by=created_by)
        unit_b = SandboxAllocationUnit.objects.create(pool=pool_b, created_by=created_by)

        request_handlers.AllocationRequestHandler().enqueue_request([unit_a], None)
        request_handlers.AllocationRequestHandler().enqueue_request([unit_b], None)

        # Run ONLY the default queue: both pools' AllocationRequests must be created
        # even though provisioning (on the ansible queue) has not run.
        get_worker('default').work(burst=True)

        assert AllocationRequest.objects.filter(allocation_unit=unit_a).exists()
        assert AllocationRequest.objects.filter(allocation_unit=unit_b).exists()

        # Provisioning was scheduled off the default queue: one job per sandbox,
        # sitting ready on the ansible queue (not run by the default worker).
        provision_jobs = [
            job
            for job in get_queue('ansible').jobs
            if (job.func_name or '').endswith('provision_netbird_for_sandbox')
        ]
        assert len(provision_jobs) == 2

        # The user-ansible stage of pool A depends on its provisioning job.
        request_a = AllocationRequest.objects.get(allocation_unit=unit_a)
        user_job = get_queue('ansible').fetch_job(
            request_a.useransibleallocationstage.rq_job.job_id
        )
        assert user_job is not None
        dependency_ids = set(user_job.dependency_ids)
        assert request_a.netbird_job_id in dependency_ids
        assert request_a.netbird_job_id in {job.id for job in provision_jobs}

    @pytest.mark.django_db(transaction=True)
    def test_no_netbird_provision_job_when_unconfigured(self, allocation_unit, mocker):
        """With NetBird unconfigured, no provisioning job is enqueued and the
        stage chain is unchanged (user-ansible depends only on networking)."""
        mocker.patch(
            'crczp.sandbox_instance_app.lib.request_handlers.netbird.is_netbird_configured',
            return_value=False,
        )

        request_handlers.AllocationRequestHandler().enqueue_request([allocation_unit], None)
        get_worker('default').work(burst=True)

        assert AllocationRequest.objects.filter(allocation_unit=allocation_unit).exists()
        provision_jobs = [
            job
            for job in get_queue('ansible').jobs
            if (job.func_name or '').endswith('provision_netbird_for_sandbox')
        ]
        assert provision_jobs == []

    def test_cancel_request(self, allocation_request_started):
        handler = request_handlers.AllocationRequestHandler()

        stop_pending = handler.cancel_request(allocation_request_started)

        assert not stop_pending
        assert allocation_request_started.is_finished
        self.stack_stop.assert_not_called()
        assert self.ansible_stop.call_count == 1

    def test_cancel_request_failed_on_completed_sandbox(self, sandbox_finished):
        handler = request_handlers.AllocationRequestHandler()

        with pytest.raises(api_exceptions.ValidationError):
            handler.cancel_request(sandbox_finished.allocation_unit.allocation_request)

        self.stack_stop.assert_not_called()
        self.ansible_stop.assert_not_called()

    def test_a_request_without_stages_is_refused_as_still_being_queued(self, allocation_request):
        handler = request_handlers.AllocationRequestHandler()

        with pytest.raises(api_exceptions.ValidationError, match='no stages yet'):
            handler.get_stage_handlers_to_cancel(allocation_request)

    def test_a_finished_request_with_a_pending_netbird_job_can_be_cancelled(
        self,
        allocation_request,
        allocation_stage_stack,
        allocation_stage_networking,
        allocation_stage_user,
    ):
        AllocationStage.objects.filter(allocation_request_fk_many=allocation_request).update(
            finished=True
        )
        AllocationRequest.objects.filter(pk=allocation_request.pk).update(netbird_job_id='netbird')
        allocation_request.refresh_from_db()
        handler = request_handlers.AllocationRequestHandler()

        handlers = handler.get_stage_handlers_to_cancel(allocation_request)

        assert [type(job) for job in handlers].count(stage_handlers.NetbirdProvisioningHandler) == 1


@pytest.mark.integration
class TestCleanupRequestHandler:
    """Integration tests for CleanupRequestHandler."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker):
        empty_queues()

        target = 'crczp.sandbox_instance_app.lib.request_handlers.CleanupStackStageHandler.execute'
        mocker.patch(target, new_callable=PicklableMock)
        self.stack_stop = mocker.patch.object(
            request_handlers.CleanupStackStageHandler, 'request_stop', new_callable=PicklableMock
        )

        target = (
            'crczp.sandbox_instance_app.lib.request_handlers.CleanupAnsibleStageHandler.execute'
        )
        mocker.patch(target, new_callable=PicklableMock)
        self.ansible_stop = mocker.patch.object(
            request_handlers.CleanupAnsibleStageHandler, 'request_stop', new_callable=PicklableMock
        )

    @pytest.mark.django_db(transaction=True)
    def test_enqueue_request(self, cleanup_request):
        handler = request_handlers.CleanupRequestHandler()

        assert cleanup_request.stages.all().count() == 0

        handler.enqueue_request(cleanup_request.allocation_unit)

        # Run only the default queue to execute _create_cleanup_jobs, which
        # creates stages and enqueues the stage jobs via on_commit.
        get_worker('default').work(burst=True)

        cleanup_request.refresh_from_db()
        assert cleanup_request.stages.all().count() == 3
        assert isinstance(cleanup_request.useransiblecleanupstage, UserAnsibleCleanupStage)
        assert isinstance(
            cleanup_request.networkingansiblecleanupstage, NetworkingAnsibleCleanupStage
        )
        assert isinstance(cleanup_request.stackcleanupstage, StackCleanupStage)

        # Capture job IDs before the last stage may delete the allocation unit.
        job_user_id = cleanup_request.useransiblecleanupstage.rq_job.job_id
        job_networking_id = cleanup_request.networkingansiblecleanupstage.rq_job.job_id
        job_stack_id = cleanup_request.stackcleanupstage.rq_job.job_id

        # Run the stage jobs.
        get_worker('openstack', 'ansible', 'default').work(burst=True)

        job_user = handler.queue_ansible.fetch_job(job_user_id)
        job_networking = handler.queue_ansible.fetch_job(job_networking_id)
        job_stack = handler.queue_stack.fetch_job(job_stack_id)

        assert job_user is not None
        assert job_networking is not None
        assert job_stack is not None
        assert job_user.is_finished
        assert job_networking.is_finished
        assert job_stack.is_finished

    def test_cancel_request(self, cleanup_request_started):
        handler = request_handlers.CleanupRequestHandler()

        stop_pending = handler.cancel_request(cleanup_request_started)

        assert not stop_pending
        assert cleanup_request_started.is_finished
        assert self.stack_stop.call_count == 1
        self.ansible_stop.assert_not_called()

    def test_cancel_request_failed_on_completed_sandbox(self, cleanup_request_finished):
        handler = request_handlers.CleanupRequestHandler()

        with pytest.raises(api_exceptions.ValidationError):
            handler.cancel_request(cleanup_request_finished)

        self.stack_stop.assert_not_called()
        self.ansible_stop.assert_not_called()


class TestRequestExceptionHandler:
    """The handler of a failed stage job of a request."""

    @pytest.fixture
    def failed_cleanup_job(self, cleanup_request, cleanup_stage_stack):
        """The job of the last cleanup stage, whose failure finished the whole request."""
        cleanup_stage_stack.finished = True
        cleanup_stage_stack.failed = True
        cleanup_stage_stack.save()
        CleanupRQJob.objects.create(job_id='stack-job', cleanup_stage=cleanup_stage_stack)
        return MagicMock(id='stack-job')

    def test_a_failure_of_the_last_cleanup_stage_retries_the_cleanup(
        self, mocker, cleanup_request, failed_cleanup_job
    ):
        retry = mocker.patch.object(request_handlers.CleanupRequestHandler, 'retry_request')

        request_handlers.request_exception_handler(failed_cleanup_job, None, None, None)

        retry.assert_called_once_with(cleanup_request)

    def test_a_failure_of_an_earlier_cleanup_stage_cancels_and_retries(
        self, mocker, cleanup_request, failed_cleanup_job
    ):
        cancel = mocker.patch.object(request_handlers.CleanupRequestHandler, 'cancel_request')
        retry = mocker.patch.object(request_handlers.CleanupRequestHandler, 'retry_request')

        request_handlers.request_exception_handler(failed_cleanup_job, None, None, None)

        cancel.assert_called_once_with(cleanup_request)
        retry.assert_called_once_with(cleanup_request)

    def test_an_allocation_failure_does_not_retry_a_cleanup(
        self, mocker, allocation_request, allocation_stage_stack
    ):
        AllocationRQJob.objects.create(job_id='alloc-job', allocation_stage=allocation_stage_stack)
        mocker.patch.object(request_handlers.AllocationRequestHandler, 'cancel_request')
        retry = mocker.patch.object(request_handlers.CleanupRequestHandler, 'retry_request')

        request_handlers.request_exception_handler(MagicMock(id='alloc-job'), None, None, None)

        retry.assert_not_called()


class TestFailLostCreation:
    """A cleanup that got stages after it was found stage-less is left alone."""

    def test_a_cleanup_with_stages_is_not_recreated_or_retried(
        self, cleanup_request, cleanup_stage_user, mocker
    ):
        handler = request_handlers.CleanupRequestHandler()
        retry = mocker.patch.object(handler, 'retry_request')
        create_handlers = mocker.patch.object(handler, '_create_stage_handlers')

        handler.fail_lost_creation(cleanup_request, 'lost')

        create_handlers.assert_not_called()
        retry.assert_not_called()
        cleanup_stage_user.refresh_from_db()
        assert not cleanup_stage_user.failed

    def test_a_stage_less_cleanup_is_failed_and_retried(self, cleanup_request, mocker):
        handler = request_handlers.CleanupRequestHandler()
        retry = mocker.patch.object(handler, 'retry_request')
        stage_handler = MagicMock()
        mocker.patch.object(handler, '_create_stage_handlers', return_value=[stage_handler])

        handler.fail_lost_creation(cleanup_request, 'lost')

        stage_handler.mark_abandoned.assert_called_once_with('lost')
        retry.assert_called_once_with(cleanup_request)


class TestRequestGroupOfUnbuiltUnits:
    """Units the creation job never reached leave the request group when they are removed."""

    def test_the_job_links_every_unit_to_the_group_before_it_builds_any(self, pool, mocker):
        pool.send_emails = True
        pool.save()
        handler = request_handlers.AllocationRequestHandler()
        built_units: list[int] = []

        def stop_after_the_first_unit(_sandbox: Any, _handlers: Any) -> None:
            built_units.append(1)
            raise RuntimeError('worker died')

        mocker.patch.object(handler, '_create_stage_handlers')
        mocker.patch.object(handler, '_enqueue_stages', side_effect=stop_after_the_first_unit)
        units = [SandboxAllocationUnit.objects.create(pool=pool) for _ in range(3)]
        for unit in units:
            AllocationRequest.objects.create(allocation_unit=unit)

        with pytest.raises(RuntimeError):
            handler._create_allocation_jobs(units, MagicMock(email='a@b.c'))

        groups = {
            AllocationRequest.objects.get(allocation_unit=unit).request_group for unit in units
        }
        assert len(groups) == 1
        assert None not in groups

    def test_removing_unbuilt_units_lowers_the_group_count_and_sends_the_summary(
        self, pool, mocker, django_capture_on_commit_callbacks
    ):
        from crczp.sandbox_instance_app.lib import allocation_cancellation

        group = SandboxRequestGroup.objects.create(
            pool=pool, unit_count=3, email='a@b.c', finished_count=1
        )
        send_summary = mocker.patch.object(SandboxRequestGroup, '_send_summary_notification')
        units = [SandboxAllocationUnit.objects.create(pool=pool) for _ in range(2)]
        for unit in units:
            AllocationRequest.objects.create(allocation_unit=unit, request_group=group)
        Pool.objects.filter(pk=pool.pk).update(size=2)

        with django_capture_on_commit_callbacks(execute=True):
            removed = allocation_cancellation._remove_stageless_units({  # pylint: disable=protected-access
                unit.id: SandboxAllocationUnit.objects.select_related('pool').get(pk=unit.id)
                for unit in units
            })

        assert len(removed) == 2
        group.refresh_from_db()
        assert group.unit_count == 1
        send_summary.assert_called_once()
