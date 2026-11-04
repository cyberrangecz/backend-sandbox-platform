"""Tests for the cancellation of stages, requests and their Cancel endpoints."""

from datetime import timedelta
from typing import Any

import django_rq
import fakeredis
import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory
from rq import Queue
from rq.exceptions import InvalidJobOperation, NoSuchJobError
from rq.job import Job, JobStatus
from rq.worker import Worker

from crczp.sandbox_ansible_app.models import Container, NetworkingAnsibleAllocationStage
from crczp.sandbox_ansible_app.serializers import NetworkingAnsibleAllocationStageSerializer
from crczp.sandbox_common_lib import exceptions as api_exceptions
from crczp.sandbox_instance_app.lib import request_handlers, stage_handlers
from crczp.sandbox_instance_app.lib import requests as sandbox_requests
from crczp.sandbox_instance_app.lib.stage_cancellation import cancel_stages
from crczp.sandbox_instance_app.models import (
    ENQUEUE_GRACE,
    AllocationRequest,
    AllocationRQJob,
    CleanupRQJob,
    SandboxAllocationUnit,
    SandboxRequestGroup,
    StackAllocationStage,
)
from crczp.sandbox_instance_app.serializers import (
    TerraformAllocationStageSerializer,
    TerraformCleanupStageSerializer,
)
from crczp.sandbox_instance_app.tests.conftest import set_stage_finished, set_stage_started
from crczp.sandbox_instance_app.views import AllocationRequestCancelView, CleanupRequestCancelView
from crczp.terraform_driver.terraform_exceptions import TerraformStateLocked

pytestmark = pytest.mark.django_db

WORKER_NAME = 'worker-one'
STAGE_HANDLERS_MODULE = 'crczp.sandbox_instance_app.lib.stage_handlers'


def noop() -> None:
    """Job body that is never executed."""


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """Serve every django_rq connection from one fresh in-memory Redis."""
    connection = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    monkeypatch.setattr(django_rq, 'get_connection', lambda *args, **kwargs: connection)
    return connection


@pytest.fixture(name='quiet_stage_handlers', autouse=True)
def fixture_quiet_stage_handlers(mocker):
    """Silence stage handler logging and stub the Ansible runner that deletes containers."""
    mocker.patch(f'{STAGE_HANDLERS_MODULE}.LOG')
    return mocker.patch(f'{STAGE_HANDLERS_MODULE}.AnsibleRunner')


@pytest.fixture(name='stop_command')
def fixture_stop_command(mocker):
    """Record the RQ stop commands sent, without a worker to receive them."""
    return mocker.patch(f'{STAGE_HANDLERS_MODULE}.send_stop_job_command')


@pytest.fixture(name='stack_stage_factory')
def fixture_stack_stage_factory(allocation_unit):
    """Return a factory of running stack stages, each in its own allocation request."""

    def create(job_id: str | None) -> StackAllocationStage:
        unit = SandboxAllocationUnit.objects.create(pool=allocation_unit.pool)
        request = AllocationRequest.objects.create(allocation_unit=unit)
        stage = StackAllocationStage.objects.create(
            allocation_request=request, allocation_request_fk_many=request
        )
        set_stage_started(stage)
        if job_id is not None:
            AllocationRQJob.objects.create(job_id=job_id, allocation_stage=stage)
        return stage

    return create


def create_started_job() -> Job:
    """Store a job executed by a live worker in Redis."""
    connection = django_rq.get_connection()
    key = Worker.redis_worker_namespace_prefix + WORKER_NAME
    connection.hset(key, 'last_heartbeat', '2026-01-01T00:00:00Z')
    connection.expire(key, 420)
    job = Job.create(noop, connection=connection)
    job.worker_name = WORKER_NAME
    job.save()
    job.set_status(JobStatus.STARTED)
    return job


def stop_job(job_id: str) -> None:
    """Mark the job stopped, as its worker does once it killed the job."""
    Job.fetch(job_id, connection=django_rq.get_connection()).set_status(JobStatus.STOPPED)


def job_exists(job_id: str) -> bool:
    """Whether Redis still holds the job."""
    try:
        Job.fetch(job_id, connection=django_rq.get_connection())
    except NoSuchJobError:
        return False
    return True


def assert_cancelled(stage: Any) -> None:
    """Assert that the stage is marked failed and finished."""
    stage.refresh_from_db()
    assert stage.finished
    assert stage.failed
    assert stage.end is not None
    assert not stage.stop_pending


class TestCancelStages:
    """Stopping the jobs of stages and confirming their end."""

    def test_queued_job_is_removed_with_its_dependents(self, stack_stage_factory, stop_command):
        """A job not started yet is deleted with its dependents, and the stage fails at once."""
        queue = Queue(connection=django_rq.get_connection())
        job = queue.enqueue(noop)
        dependent = queue.enqueue(noop, depends_on=job)
        stage = stack_stage_factory(job.id)

        pending = cancel_stages([stage_handlers.AllocationStackStageHandler(stage)], 0)

        assert pending == []
        assert not job_exists(job.id)
        assert not job_exists(dependent.id)
        stop_command.assert_not_called()
        assert_cancelled(stage)
        assert stage.stop_requested_at is None

    def test_started_job_gets_the_stop_command(self, stack_stage_factory, stop_command):
        """An executing job gets the stop command; its stage fails once the job reads ended."""
        job = create_started_job()
        dependent = Queue(connection=django_rq.get_connection()).enqueue(noop, depends_on=job)
        stage = stack_stage_factory(job.id)
        stop_command.side_effect = lambda _connection, job_id: stop_job(job_id)

        pending = cancel_stages([stage_handlers.AllocationStackStageHandler(stage)], 5)

        assert pending == []
        stop_command.assert_called_once_with(django_rq.get_connection(), job.id)
        assert not job_exists(dependent.id)
        assert_cancelled(stage)
        assert stage.stop_requested_at is not None

    def test_unconfirmed_stop_stays_pending(self, stack_stage_factory, stop_command):
        """A job outliving the wait leaves its stage unfinished and stop pending."""
        job = create_started_job()
        stage = stack_stage_factory(job.id)
        handler = stage_handlers.AllocationStackStageHandler(stage)

        pending = cancel_stages([handler], 0)

        assert pending == [handler]
        stop_command.assert_called_once()
        stage.refresh_from_db()
        assert not stage.finished
        assert stage.stop_pending
        assert stage.stop_requested_at is not None

    def test_repeated_cancel_keeps_the_first_stop_time(self, stack_stage_factory, stop_command):
        """A later Cancel of a stop-pending stage keeps when the stop was first requested."""
        job = create_started_job()
        stage = stack_stage_factory(job.id)
        cancel_stages([stage_handlers.AllocationStackStageHandler(stage)], 0)
        stage.refresh_from_db()
        first_requested_at = stage.stop_requested_at
        stop_job(job.id)

        pending = cancel_stages([stage_handlers.AllocationStackStageHandler(stage)], 0)

        assert pending == []
        assert stop_command.call_count == 1
        assert_cancelled(stage)
        assert stage.stop_requested_at == first_requested_at

    def test_stage_finished_by_its_job_is_kept(self, stack_stage_factory, stop_command):
        """A stage its job finished while the stop was sent keeps the job's outcome."""
        job = create_started_job()
        stage = stack_stage_factory(job.id)

        def finish_stage(_connection: Any, job_id: str) -> None:
            set_stage_finished(StackAllocationStage.objects.get(pk=stage.pk))
            stop_job(job_id)

        stop_command.side_effect = finish_stage

        cancel_stages([stage_handlers.AllocationStackStageHandler(stage)], 5)

        stage.refresh_from_db()
        assert stage.finished
        assert not stage.failed

    def test_finished_stage_is_skipped(self, stack_stage_factory, stop_command):
        """A finished stage is neither stopped nor marked failed."""
        job = create_started_job()
        stage = stack_stage_factory(job.id)
        set_stage_finished(stage)

        assert cancel_stages([stage_handlers.AllocationStackStageHandler(stage)], 0) == []

        stop_command.assert_not_called()
        stage.refresh_from_db()
        assert not stage.failed

    def test_stage_whose_job_is_yet_to_be_queued_is_stopped_once_queued(
        self, stack_stage_factory, stop_command
    ):
        """A stage waiting for its job stays pending, and its job is removed once stored."""
        stage = stack_stage_factory(None)
        stage.job_requested_at = timezone.now()
        stage.save()
        handler = stage_handlers.AllocationStackStageHandler(stage)

        assert cancel_stages([handler], 0) == [handler]
        assert not StackAllocationStage.objects.get(pk=stage.pk).finished

        job = Queue(connection=django_rq.get_connection()).enqueue(noop)
        AllocationRQJob.objects.create(job_id=job.id, allocation_stage=stage)

        assert cancel_stages([handler], 1) == []
        assert not job_exists(job.id)
        stop_command.assert_not_called()
        assert_cancelled(stage)

    def test_stage_whose_job_was_never_queued_is_cancelled(self, stack_stage_factory):
        """A stage still without its job past the enqueue grace fails at once."""
        stage = stack_stage_factory(None)
        stage.job_requested_at = timezone.now() - ENQUEUE_GRACE - timedelta(seconds=1)
        stage.save()

        assert cancel_stages([stage_handlers.AllocationStackStageHandler(stage)], 0) == []

        assert_cancelled(stage)

    def test_queued_job_a_worker_took_gets_the_stop_command(
        self, stack_stage_factory, stop_command
    ):
        """A job read as queued but no longer on its queue is stopped instead of deleted."""
        job = Job.create(noop, connection=django_rq.get_connection(), origin='default')
        job.save()
        job.set_status(JobStatus.QUEUED)
        stage = stack_stage_factory(job.id)

        cancel_stages([stage_handlers.AllocationStackStageHandler(stage)], 0)

        stop_command.assert_called_with(django_rq.get_connection(), job.id)
        assert job_exists(job.id)

    def test_stop_not_sent_is_not_recorded(self, stack_stage_factory, stop_command):
        """A stop command the job could not take leaves the stop unrecorded, to be sent again."""
        job = create_started_job()
        stage = stack_stage_factory(job.id)
        stop_command.side_effect = InvalidJobOperation('not executing')
        handler = stage_handlers.AllocationStackStageHandler(stage)

        assert cancel_stages([handler], 0) == [handler]

        assert not handler.stop_sent
        assert stop_command.call_count == 2
        stage.refresh_from_db()
        assert stage.stop_requested_at is None

    def test_ansible_stage_deletes_its_container(
        self, allocation_stage_networking_started, quiet_stage_handlers
    ):
        """Cancelling an Ansible stage deletes its Docker container and the container row."""
        handler = stage_handlers.AllocationAnsibleStageHandler(allocation_stage_networking_started)

        assert cancel_stages([handler], 0) == []

        quiet_stage_handlers.return_value.delete_container.assert_called_once_with(
            'docker-container-id'
        )
        assert not Container.objects.filter(
            allocation_stage=allocation_stage_networking_started
        ).exists()
        assert_cancelled(allocation_stage_networking_started)


class TestRequestGroupCompletion:
    """A sandbox build stopped before it finished still counts in its request group."""

    @pytest.fixture(name='summary')
    def fixture_summary(self, mocker):
        """Stub the group e-mails and return the summary sender."""
        mocker.patch.object(SandboxRequestGroup, '_send_fail_notification')
        return mocker.patch.object(SandboxRequestGroup, '_send_summary_notification')

    @staticmethod
    def join_group(stage: StackAllocationStage, unit_count: int = 1) -> SandboxRequestGroup:
        """Link the allocation request of the stage to a new request group."""
        request = stage.allocation_request_fk_many
        group = SandboxRequestGroup.objects.create(
            pool=request.allocation_unit.pool, unit_count=unit_count, email='a@b.c'
        )
        request.request_group = group
        request.save(update_fields=['request_group'])
        return group

    @staticmethod
    def assert_counts(group: SandboxRequestGroup, finished: int, failed: int) -> None:
        """Assert the finished and failed build counts of the group."""
        group.refresh_from_db()
        assert (group.finished_count, group.failed_count) == (finished, failed)

    def test_a_cancelled_build_completes_its_group(
        self, stack_stage_factory, summary, django_capture_on_commit_callbacks
    ):
        """A build cancelled before its job started fails in its group, which sends its summary."""
        job = Queue(connection=django_rq.get_connection()).enqueue(noop)
        stage = stack_stage_factory(job.id)
        group = self.join_group(stage)

        with django_capture_on_commit_callbacks(execute=True):
            cancel_stages([stage_handlers.AllocationStackStageHandler(stage)], 0)

        self.assert_counts(group, finished=1, failed=1)
        summary.assert_called_once_with()

    def test_a_build_killed_by_the_stop_command_completes_its_group(
        self, stack_stage_factory, stop_command, summary, django_capture_on_commit_callbacks
    ):
        """A build whose running job the stop command killed fails in its group."""
        job = create_started_job()
        stage = stack_stage_factory(job.id)
        group = self.join_group(stage)
        stop_command.side_effect = lambda _connection, job_id: stop_job(job_id)

        with django_capture_on_commit_callbacks(execute=True):
            cancel_stages([stage_handlers.AllocationStackStageHandler(stage)], 5)

        self.assert_counts(group, finished=1, failed=1)
        summary.assert_called_once_with()

    def test_a_build_counts_once(
        self, mocker, stack_stage_factory, summary, django_capture_on_commit_callbacks
    ):
        """A build failed by its own stage and then cancelled counts in its group once."""
        stage = stack_stage_factory(None)
        group = self.join_group(stage, unit_count=2)
        request = stage.allocation_request_fk_many
        next_stage = NetworkingAnsibleAllocationStage.objects.create(
            allocation_request=request,
            allocation_request_fk_many=request,
            repo_url='url',
            rev='rev',
        )
        mocker.patch.object(
            stage_handlers.AllocationStackStageHandler,
            '_execute',
            side_effect=TerraformStateLocked('lock-id'),
        )

        with django_capture_on_commit_callbacks(execute=True):
            with pytest.raises(TerraformStateLocked):
                stage_handlers.AllocationStackStageHandler(stage).execute()
            stage_handlers.AllocationStackStageHandler(stage).mark_cancelled()
            stage_handlers.AllocationAnsibleStageHandler(next_stage).mark_cancelled()

        self.assert_counts(group, finished=1, failed=1)
        summary.assert_not_called()


class TestCancelRequests:
    """Cancelling whole requests."""

    def test_cancel_request_answers_stop_pending(self, allocation_request_started, stop_command):
        """The request answers stop pending while one of its stage jobs outlives the wait."""
        user_stage = allocation_request_started.useransibleallocationstage
        user_stage.rq_job.job_id = create_started_job().id
        user_stage.rq_job.save()

        stop_pending = request_handlers.AllocationRequestHandler().cancel_request(
            allocation_request_started, 0
        )

        assert stop_pending
        stop_command.assert_called_once()
        user_stage.refresh_from_db()
        assert user_stage.stop_pending

    def test_cancel_request_of_finished_request_is_refused(self, cleanup_request_finished):
        """A finished request cannot be cancelled."""
        with pytest.raises(api_exceptions.ValidationError):
            request_handlers.CleanupRequestHandler().cancel_request(cleanup_request_finished, 0)

    def test_cancel_requests_answers_per_request(
        self, allocation_request_started, cleanup_request_started, stop_command
    ):
        """The many-request form answers whether a stop is pending for each request in turn."""
        cleanup_stage = cleanup_request_started.stackcleanupstage
        AllocationRQJob.objects.filter(
            allocation_stage=allocation_request_started.useransibleallocationstage
        ).delete()
        CleanupRQJob.objects.create(job_id=create_started_job().id, cleanup_stage=cleanup_stage)

        answers = request_handlers.cancel_requests(
            [allocation_request_started, cleanup_request_started], 0
        )

        assert answers == [False, True]
        stop_command.assert_called_once()
        assert allocation_request_started.is_finished
        assert not cleanup_request_started.is_finished

    def test_cancel_requests_skips_a_finished_request(
        self, allocation_request_started, cleanup_request_finished, stop_command
    ):
        """A request that finished meanwhile answers None and does not stop the others."""
        AllocationRQJob.objects.filter(
            allocation_stage=allocation_request_started.useransibleallocationstage
        ).delete()

        answers = request_handlers.cancel_requests(
            [cleanup_request_finished, allocation_request_started], 0
        )

        assert answers == [None, False]
        assert allocation_request_started.is_finished


class TestNetbirdProvisioningCancel:
    """Cancelling the NetBird provisioning job tracked on an allocation request."""

    @pytest.fixture(name='tracked_request')
    def fixture_tracked_request(self, allocation_request_started):
        """An unfinished allocation request whose stages have no live job."""
        AllocationRQJob.objects.filter(
            allocation_stage__allocation_request_fk_many=allocation_request_started
        ).delete()
        return allocation_request_started

    @staticmethod
    def track(allocation_request: AllocationRequest, job_id: str) -> None:
        """Store the provisioning job id on the request."""
        allocation_request.netbird_job_id = job_id
        allocation_request.save()

    def test_queued_job_is_removed_with_its_dependents(self, tracked_request, stop_command):
        """A provisioning job not started yet is deleted, and the request forgets it at once."""
        queue = Queue(connection=django_rq.get_connection())
        job = queue.enqueue(noop)
        dependent = queue.enqueue(noop, depends_on=job)
        self.track(tracked_request, job.id)

        pending = cancel_stages([stage_handlers.NetbirdProvisioningHandler(tracked_request)], 0)

        assert pending == []
        assert not job_exists(job.id)
        assert not job_exists(dependent.id)
        stop_command.assert_not_called()
        tracked_request.refresh_from_db()
        assert tracked_request.netbird_job_id is None

    def test_running_job_is_stopped_and_forgotten_once_dead(self, tracked_request, stop_command):
        """A running job gets the stop command and stays tracked until it reads dead."""
        job = create_started_job()
        self.track(tracked_request, job.id)
        handler = stage_handlers.NetbirdProvisioningHandler(tracked_request)

        pending = cancel_stages([handler], 0)

        assert pending == [handler]
        stop_command.assert_called_once()
        tracked_request.refresh_from_db()
        assert tracked_request.netbird_job_id == job.id

        stop_job(job.id)

        assert cancel_stages([handler], 0) == []
        tracked_request.refresh_from_db()
        assert tracked_request.netbird_job_id is None

    def test_missing_job_is_forgotten(self, tracked_request, stop_command):
        """A job that Redis lost reads dead; nothing is stopped and the request forgets it."""
        self.track(tracked_request, 'lost-job')

        pending = cancel_stages([stage_handlers.NetbirdProvisioningHandler(tracked_request)], 0)

        assert pending == []
        stop_command.assert_not_called()
        tracked_request.refresh_from_db()
        assert tracked_request.netbird_job_id is None

    def test_request_handlers_include_the_job_only_while_tracked(self, tracked_request):
        """The allocation request lists the provisioning handler next to its stage handlers."""
        handler = request_handlers.AllocationRequestHandler()
        untracked = handler.get_stage_handlers_to_cancel(tracked_request)
        self.track(tracked_request, 'netbird-job')

        tracked = handler.get_stage_handlers_to_cancel(tracked_request)

        assert not any(
            isinstance(entry, stage_handlers.NetbirdProvisioningHandler) for entry in untracked
        )
        assert [
            entry
            for entry in tracked
            if isinstance(entry, stage_handlers.NetbirdProvisioningHandler)
        ]

    def test_job_yet_to_be_queued_is_waited_for(self, tracked_request, stop_command):
        """While a stage of the request waits for its job, the provisioning job may follow."""
        stage = tracked_request.stackallocationstage
        stage.job_requested_at = timezone.now()
        stage.save()
        handler = stage_handlers.NetbirdProvisioningHandler(tracked_request)

        assert any(
            isinstance(entry, stage_handlers.NetbirdProvisioningHandler)
            for entry in request_handlers.AllocationRequestHandler().get_stage_handlers_to_cancel(
                tracked_request
            )
        )
        assert cancel_stages([handler], 0) == [handler]

        job = Queue(connection=django_rq.get_connection()).enqueue(noop)
        AllocationRequest.objects.filter(pk=tracked_request.pk).update(netbird_job_id=job.id)

        assert cancel_stages([handler], 1) == []
        assert not job_exists(job.id)
        stop_command.assert_not_called()

    def test_running_job_makes_the_request_stop_pending(self, tracked_request, stop_command):
        """Cancel of the request stops the provisioning job and counts it as pending."""
        self.track(tracked_request, create_started_job().id)

        stop_pending = request_handlers.AllocationRequestHandler().cancel_request(
            tracked_request, 0
        )

        assert stop_pending
        stop_command.assert_called_once()


class TestStateLockRelease:
    """The cleanup's destroy of a stack whose state lock is held."""

    @pytest.fixture
    def client(self, mocker):
        """Stub the Terraform client of the cleanup stack stage handler."""
        client = mocker.Mock()
        mocker.patch.object(stage_handlers.CleanupStackStageHandler, '_client', client)
        return client

    def test_lock_of_ended_allocation_is_released(
        self, mocker, client, cleanup_stage_stack_started
    ):
        """A destroy blocked by the lock of an ended allocation releases it and runs again."""
        destroy = mocker.patch.object(
            stage_handlers.CleanupStackStageHandler,
            '_destroy_stack',
            side_effect=[TerraformStateLocked('lock-id'), True],
        )
        handler = stage_handlers.CleanupStackStageHandler(cleanup_stage_stack_started)
        unit = cleanup_stage_stack_started.cleanup_request.allocation_unit
        unit.pool.size = 1
        unit.pool.save()

        handler.execute()

        client.force_unlock_stack.assert_called_once_with(unit.get_stack_name(), 'lock-id')
        assert destroy.call_count == 2
        assert not SandboxAllocationUnit.objects.filter(pk=unit.pk).exists()

    def test_lock_of_running_allocation_is_kept(self, mocker, client, cleanup_stage_stack_started):
        """A destroy blocked while the allocation stack stage runs fails without unlocking."""
        allocation_stage = (
            cleanup_stage_stack_started.cleanup_request.allocation_unit.allocation_request
        ).stackallocationstage
        StackAllocationStage.objects.filter(pk=allocation_stage.pk).update(finished=False)
        AllocationRQJob.objects.update_or_create(
            allocation_stage=allocation_stage, defaults={'job_id': create_started_job().id}
        )
        mocker.patch.object(
            stage_handlers.CleanupStackStageHandler,
            '_destroy_stack',
            side_effect=TerraformStateLocked('lock-id'),
        )
        handler = stage_handlers.CleanupStackStageHandler(cleanup_stage_stack_started)

        with pytest.raises(api_exceptions.StackError):
            handler.execute()

        client.force_unlock_stack.assert_not_called()

    def test_wait_reads_the_lock_id_from_the_output(self, mocker, cleanup_stage_stack):
        """A failed OpenTofu run blocked by a state lock raises the lock id."""
        handler = stage_handlers.CleanupStackStageHandler(cleanup_stage_stack)
        handler._client = mocker.Mock()  # pylint: disable=protected-access
        handler._client.wait_for_process.return_value = ('', '', 1)  # pylint: disable=protected-access
        logged_output = 'Error: Error acquiring the state lock\nLock Info:\n  ID:        abc-123'

        with pytest.raises(TerraformStateLocked) as raised:
            stage_handlers.StackStageHandler._wait_for_process(  # pylint: disable=protected-access
                handler, mocker.Mock(), logged_output=logged_output
            )

        assert raised.value.lock_id == 'abc-123'


class TestStopPendingExposure:
    """Stage responses and the Cancel endpoints."""

    def test_stage_serializers_expose_the_stop(self, stack_stage_factory, stop_command):
        """Stage responses carry stop_pending and stop_requested_at."""
        stage = stack_stage_factory(create_started_job().id)
        idle = TerraformAllocationStageSerializer(stage).data
        cancel_stages([stage_handlers.AllocationStackStageHandler(stage)], 0)
        stage.refresh_from_db()

        pending = TerraformAllocationStageSerializer(stage).data

        assert idle['stop_pending'] is False
        assert idle['stop_requested_at'] is None
        assert pending['stop_pending'] is True
        assert pending['stop_requested_at'] is not None
        stop_command.assert_called_once()

    def test_every_stage_serializer_carries_the_fields(
        self, cleanup_stage_stack, allocation_stage_networking
    ):
        """Stack and Ansible stage serializers of both request kinds carry the stop fields."""
        for data in (
            TerraformCleanupStageSerializer(cleanup_stage_stack).data,
            NetworkingAnsibleAllocationStageSerializer(allocation_stage_networking).data,
        ):
            assert data['stop_pending'] is False
            assert data['stop_requested_at'] is None

    @pytest.mark.parametrize('stop_pending', [True, False])
    def test_allocation_cancel_queues_cleanup_once_confirmed(
        self, mocker, allocation_request, allocation_stage_stack, stop_pending
    ):
        """The allocation Cancel answers stop_pending and queues the cleanup only once confirmed."""
        mocker.patch.object(
            sandbox_requests, 'cancel_allocation_request', return_value=stop_pending
        )
        create_cleanup = mocker.patch.object(sandbox_requests, 'create_cleanup_request')

        response = AllocationRequestCancelView.as_view()(
            APIRequestFactory().patch('/'), request_id=allocation_request.id
        )

        assert response.status_code == 200
        assert response.data == {'stop_pending': stop_pending}
        assert create_cleanup.called is not stop_pending

    def test_cleanup_cancel_answers_stop_pending(self, mocker, cleanup_request):
        """The cleanup Cancel answers stop_pending and queues nothing."""
        mocker.patch.object(sandbox_requests, 'cancel_cleanup_request', return_value=True)

        response = CleanupRequestCancelView.as_view()(
            APIRequestFactory().patch('/'), request_id=cleanup_request.id
        )

        assert response.status_code == 200
        assert response.data == {'stop_pending': True}

    def test_forced_cleanup_waits_for_a_pending_stop(self, mocker, allocation_request_started):
        """A forced cleanup of a unit whose allocation stop is pending is refused."""
        mocker.patch.object(sandbox_requests, 'cancel_allocation_request', return_value=True)

        with pytest.raises(sandbox_requests.CleanupNotAllowedError):
            sandbox_requests.create_cleanup_request_force(
                allocation_request_started.allocation_unit, delete_pool=False
            )
