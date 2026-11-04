"""Tests of the detection and recovery of work whose RQ job was lost."""

# pylint: disable=missing-function-docstring,redefined-outer-name
import copy
from datetime import timedelta

import django_rq
import fakeredis
import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from rq import Queue
from rq.job import Job, JobStatus
from rq.worker import Worker

from crczp.sandbox_instance_app.lib import lost_jobs, request_handlers, requests
from crczp.sandbox_instance_app.lib.lost_jobs import LostWorkKind
from crczp.sandbox_instance_app.lib.stage_handlers import (
    JOB_LOST_MESSAGE,
    NETBIRD_JOB_LOST_MESSAGE,
    PRECEDING_STAGE_FAILED_MESSAGE,
    WORKER_DIED_MESSAGE,
)
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    AllocationRQJob,
    AllocationStage,
    CleanupRequest,
    CleanupRQJob,
    CleanupStage,
    SandboxAllocationUnit,
)

pytestmark = pytest.mark.django_db

GRACE = timedelta(seconds=900)
LONG_AGO = timezone.now() - timedelta(days=1)


SEEDED_UNIT_IDS: set[int] = set()


def find_work() -> list[lost_jobs.LostWork]:
    """The lost work of the units the test created, leaving out the seeded ones."""
    return [work for work in lost_jobs.find_lost_work(GRACE) if work.unit_id not in SEEDED_UNIT_IDS]


@pytest.fixture(autouse=True)
def remember_seeded_units(db):  # pylint: disable=unused-argument
    SEEDED_UNIT_IDS.clear()
    SEEDED_UNIT_IDS.update(SandboxAllocationUnit.objects.values_list('pk', flat=True))


def noop() -> None:
    """Job body that is never executed."""


@pytest.fixture(autouse=True)
def connection(monkeypatch) -> fakeredis.FakeRedis:
    fake_connection = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    monkeypatch.setattr(django_rq, 'get_connection', lambda *args, **kwargs: fake_connection)
    monkeypatch.setattr(
        request_handlers.RequestHandler,
        'queue_default',
        Queue('default', connection=fake_connection),
    )
    return fake_connection


def store_job(
    connection,
    status: JobStatus,
    *,
    worker_name: str | None = None,
    last_activity=LONG_AGO,
) -> Job:
    job = Job.create(noop, connection=connection)
    job.worker_name = worker_name
    job.created_at = last_activity
    job.enqueued_at = last_activity
    job.started_at = last_activity
    job.last_heartbeat = last_activity
    job.save()
    job.set_status(status)
    return job


def keep_worker_alive(connection, name: str) -> None:
    key = Worker.redis_worker_namespace_prefix + name
    connection.hset(key, 'last_heartbeat', '2026-01-01T00:00:00Z')
    connection.expire(key, 420)


def attach_job(stage, job_id: str) -> None:
    AllocationRQJob.objects.create(job_id=job_id, allocation_stage=stage)


def find_kinds() -> list[LostWorkKind]:
    return [work.kind for work in find_work()]


class TestLostStages:
    def test_a_stage_whose_job_is_gone_from_redis_is_lost_at_once(
        self, allocation_stage_stack, allocation_request
    ):
        attach_job(allocation_stage_stack, 'vanished')

        [work] = find_work()

        assert work.kind is LostWorkKind.ALLOCATION_STAGE
        assert work.unit_id == allocation_request.allocation_unit.pk
        assert work.reason == JOB_LOST_MESSAGE

    def test_a_dead_worker_is_lost_once_the_grace_has_passed(
        self, connection, allocation_stage_stack
    ):
        job = store_job(connection, JobStatus.STARTED, worker_name='gone')
        attach_job(allocation_stage_stack, job.id)

        [work] = find_work()

        assert work.reason == WORKER_DIED_MESSAGE

    def test_a_dead_worker_is_left_to_rq_within_the_grace(self, connection, allocation_stage_stack):
        job = store_job(
            connection, JobStatus.STARTED, worker_name='gone', last_activity=timezone.now()
        )
        attach_job(allocation_stage_stack, job.id)

        assert not find_work()

    def test_a_failed_job_within_the_grace_is_not_lost(self, connection, allocation_stage_stack):
        job = store_job(connection, JobStatus.FAILED, last_activity=timezone.now())
        attach_job(allocation_stage_stack, job.id)

        assert not find_work()

    @pytest.mark.parametrize('status', [JobStatus.QUEUED, JobStatus.DEFERRED])
    def test_a_waiting_job_is_never_lost(self, connection, allocation_stage_stack, status):
        attach_job(allocation_stage_stack, store_job(connection, status).id)

        assert not find_work()

    def test_a_job_on_a_living_worker_is_never_lost(self, connection, allocation_stage_stack):
        job = store_job(connection, JobStatus.STARTED, worker_name='alive')
        keep_worker_alive(connection, 'alive')
        attach_job(allocation_stage_stack, job.id)

        assert not find_work()

    def test_a_stage_whose_job_is_yet_to_be_queued_is_not_lost(self, allocation_stage_stack):
        allocation_stage_stack.job_requested_at = timezone.now()
        allocation_stage_stack.save()

        assert not find_work()

    def test_a_stage_whose_job_id_is_stored_but_not_yet_queued_is_not_lost(
        self, allocation_stage_stack
    ):
        allocation_stage_stack.job_requested_at = timezone.now()
        allocation_stage_stack.save()
        attach_job(allocation_stage_stack, 'about-to-be-queued')

        assert not find_work()

    def test_a_stage_with_a_pending_stop_is_left_to_the_person_who_stopped_it(
        self, allocation_stage_stack
    ):
        attach_job(allocation_stage_stack, 'vanished')
        allocation_stage_stack.stop_requested_at = timezone.now()
        allocation_stage_stack.save()

        assert not find_work()

    def test_only_the_first_unfinished_stage_is_judged(
        self, allocation_stage_stack, allocation_stage_networking
    ):
        attach_job(allocation_stage_stack, 'vanished')
        attach_job(allocation_stage_networking, 'vanished-too')

        [work] = find_work()

        assert work.kind is LostWorkKind.ALLOCATION_STAGE

    @pytest.mark.parametrize(('failed_long_ago', 'expected_lost'), [(True, True), (False, False)])
    def test_a_cleanup_stage_waiting_behind_a_failed_stage_is_lost_past_the_grace(
        self,
        connection,
        cleanup_stage_user,
        cleanup_stage_networking,
        failed_long_ago,
        expected_lost,
    ):
        CleanupStage.objects.filter(pk=cleanup_stage_user.pk).update(
            failed=True, finished=True, end=LONG_AGO if failed_long_ago else timezone.now()
        )
        job = store_job(connection, JobStatus.DEFERRED)
        CleanupRQJob.objects.create(job_id=job.id, cleanup_stage=cleanup_stage_networking)

        works = find_work()

        assert [(work.kind, work.reason) for work in works] == (
            [(LostWorkKind.CLEANUP_STAGE, PRECEDING_STAGE_FAILED_MESSAGE)] if expected_lost else []
        )

    def test_a_finished_stage_is_not_judged(self, allocation_stage_stack):
        attach_job(allocation_stage_stack, 'vanished')
        allocation_stage_stack.finished = True
        allocation_stage_stack.save()

        assert not find_work()

    def test_a_user_stage_awaiting_a_dead_netbird_job_is_lost(
        self, connection, allocation_request, allocation_stage_user
    ):
        deferred = store_job(connection, JobStatus.DEFERRED)
        attach_job(allocation_stage_user, deferred.id)
        allocation_request.netbird_job_id = store_job(connection, JobStatus.FAILED).id
        allocation_request.save()

        [work] = find_work()

        assert work.reason == NETBIRD_JOB_LOST_MESSAGE

    def test_a_user_stage_awaiting_a_missing_netbird_job_is_not_lost(
        self, connection, allocation_request, allocation_stage_user
    ):
        attach_job(allocation_stage_user, store_job(connection, JobStatus.DEFERRED).id)
        allocation_request.netbird_job_id = 'vanished'
        allocation_request.save()

        assert not find_work()


class TestStagelessRequests:
    def test_a_stageless_allocation_without_a_creation_job_is_lost(self, allocation_request):
        AllocationRequest.objects.filter(pk=allocation_request.pk).update(created=LONG_AGO)

        assert find_kinds() == [LostWorkKind.ALLOCATION_CREATION]

    def test_a_young_stageless_allocation_is_not_lost(self, allocation_request):
        assert not find_kinds()

    def test_a_stageless_allocation_with_a_queued_creation_job_is_not_lost(
        self, connection, allocation_request, allocation_unit
    ):
        AllocationRequest.objects.filter(pk=allocation_request.pk).update(created=LONG_AGO)
        request_handlers.RequestHandler.queue_default.enqueue(
            request_handlers.AllocationRequestHandler()._create_allocation_jobs,
            [allocation_unit],
            None,
        )

        assert not find_kinds()

    def test_a_stageless_allocation_is_removed_with_its_unit(self, allocation_request):
        AllocationRequest.objects.filter(pk=allocation_request.pk).update(created=LONG_AGO)
        unit_id = allocation_request.allocation_unit.pk
        [work] = find_work()

        assert not lost_jobs.recover_lost_work([work])
        assert not SandboxAllocationUnit.objects.filter(pk=unit_id).exists()

    def test_a_stageless_cleanup_without_a_creation_job_is_lost(self, allocation_unit):
        CleanupRequest.objects.create(allocation_unit=allocation_unit, created=LONG_AGO)

        assert find_kinds() == [LostWorkKind.CLEANUP_CREATION]

    def test_a_stageless_cleanup_with_a_queued_creation_job_is_not_lost(self, allocation_unit):
        CleanupRequest.objects.create(allocation_unit=allocation_unit, created=LONG_AGO)
        request_handlers.RequestHandler.queue_default.enqueue(
            request_handlers.CleanupRequestHandler()._create_cleanup_jobs, allocation_unit
        )

        assert not find_kinds()


class TestAbandonedAllocations:
    def failed_allocation(self, allocation_stage_stack, message: str, end=LONG_AGO):
        allocation_stage_stack.failed = True
        allocation_stage_stack.finished = True
        allocation_stage_stack.error_message = message
        allocation_stage_stack.end = end
        allocation_stage_stack.save()

    def test_an_abandoned_failure_without_a_cleanup_is_found(self, allocation_stage_stack):
        self.failed_allocation(allocation_stage_stack, WORKER_DIED_MESSAGE)

        assert find_kinds() == [LostWorkKind.ABANDONED_ALLOCATION]

    def test_an_ordinary_failure_is_left_for_the_person(self, allocation_stage_stack):
        self.failed_allocation(allocation_stage_stack, 'Terraform failed')

        assert not find_kinds()

    def test_a_recent_abandoned_failure_is_left_to_the_failure_callback(
        self, allocation_stage_stack
    ):
        self.failed_allocation(allocation_stage_stack, WORKER_DIED_MESSAGE, end=timezone.now())

        assert not find_kinds()

    def test_an_abandoned_failure_with_a_cleanup_is_not_found(
        self, allocation_stage_stack, allocation_unit
    ):
        self.failed_allocation(allocation_stage_stack, WORKER_DIED_MESSAGE)
        CleanupRequest.objects.create(allocation_unit=allocation_unit)

        assert not find_kinds()

    def test_the_cleanup_is_queued_by_the_recovery(self, mocker, allocation_stage_stack):
        self.failed_allocation(allocation_stage_stack, JOB_LOST_MESSAGE)
        force = mocker.patch.object(requests, 'create_cleanup_request_force')
        [work] = find_work()

        assert not lost_jobs.recover_lost_work([work])
        force.assert_called_once_with(
            allocation_stage_stack.allocation_request_fk_many.allocation_unit, delete_pool=False
        )


class TestRecovery:
    def test_an_allocation_stage_is_failed_through_the_handler(
        self, mocker, allocation_stage_stack
    ):
        attach_job(allocation_stage_stack, 'vanished')
        fail = mocker.patch.object(request_handlers.AllocationRequestHandler, 'fail_lost_stage')
        [work] = find_work()

        assert not lost_jobs.recover_lost_work([work])
        fail.assert_called_once()
        assert fail.call_args.args[1:] == (allocation_stage_stack.pk, JOB_LOST_MESSAGE)

    def test_an_allocation_stage_stopped_meanwhile_is_not_failed_by_the_recovery(
        self,
        mocker,
        allocation_stage_stack,
        allocation_stage_networking,
        allocation_stage_user,
    ):
        attach_job(allocation_stage_stack, 'vanished')
        [work] = find_work()
        AllocationStage.objects.filter(pk=allocation_stage_stack.pk).update(
            stop_requested_at=timezone.now()
        )
        cleanup = mocker.patch.object(requests, 'create_cleanup_request_force')
        cleanup_normal = mocker.patch.object(requests, 'create_cleanup_request')

        assert not lost_jobs.recover_lost_work([work])

        allocation_stage_stack.refresh_from_db()
        assert not allocation_stage_stack.finished
        cleanup.assert_not_called()
        cleanup_normal.assert_not_called()

    def test_a_cleanup_stage_stopped_meanwhile_is_not_failed_by_the_recovery(
        self, mocker, cleanup_stage_user, cleanup_stage_networking, cleanup_stage_stack
    ):
        CleanupRQJob.objects.create(job_id='vanished', cleanup_stage=cleanup_stage_user)
        [work] = find_work()
        CleanupStage.objects.filter(pk=cleanup_stage_user.pk).update(
            stop_requested_at=timezone.now()
        )
        retry = mocker.patch.object(request_handlers.CleanupRequestHandler, 'retry_request')

        assert not lost_jobs.recover_lost_work([work])

        cleanup_stage_user.refresh_from_db()
        assert not cleanup_stage_user.finished
        retry.assert_not_called()

    def test_a_dry_run_changes_nothing(self, mocker, allocation_stage_stack):
        attach_job(allocation_stage_stack, 'vanished')
        fail = mocker.patch.object(request_handlers.AllocationRequestHandler, 'fail_lost_stage')

        assert not lost_jobs.recover_lost_work(find_work(), dry_run=True)
        fail.assert_not_called()

    def test_a_failing_recovery_does_not_stop_the_others(self, mocker, allocation_unit):
        mocker.patch.object(
            request_handlers.CleanupRequestHandler,
            'fail_lost_creation',
            side_effect=[RuntimeError('boom'), None],
        )
        other_unit = SandboxAllocationUnit.objects.create(pool=allocation_unit.pool)
        for unit in (allocation_unit, other_unit):
            CleanupRequest.objects.create(allocation_unit=unit, created=LONG_AGO)
        work = find_work()

        failed = lost_jobs.recover_lost_work(work)

        assert len(work) == 2
        assert len(failed) == 1

    def test_a_stageless_cleanup_is_retried_after_its_stages_are_failed(
        self, mocker, allocation_unit
    ):
        request = CleanupRequest.objects.create(allocation_unit=allocation_unit, created=LONG_AGO)
        retry = mocker.patch.object(request_handlers.CleanupRequestHandler, 'retry_request')
        [work] = find_work()

        assert not lost_jobs.recover_lost_work([work])
        retry.assert_called_once()
        request.refresh_from_db()
        assert request.is_finished
        assert all(stage.failed for stage in request.stages.all())
        assert request.stages.count() == 3


class TestObserveLostJobsCommand:
    def test_it_does_nothing_while_disabled(self, mocker, allocation_stage_stack):
        attach_job(allocation_stage_stack, 'vanished')
        config = copy.copy(settings.CRCZP_CONFIG)
        config.lost_job_observer = copy.copy(config.lost_job_observer)
        config.lost_job_observer.enabled = False
        mocker.patch.object(settings, 'CRCZP_CONFIG', config)
        fail = mocker.patch.object(request_handlers.AllocationRequestHandler, 'fail_lost_stage')

        call_command('observe_lost_jobs')

        fail.assert_not_called()

    def test_it_recovers_the_lost_work(self, mocker, allocation_stage_stack):
        attach_job(allocation_stage_stack, 'vanished')
        fail = mocker.patch.object(request_handlers.AllocationRequestHandler, 'fail_lost_stage')

        call_command('observe_lost_jobs')

        assert allocation_stage_stack.pk in [call.args[1] for call in fail.call_args_list]

    def test_dry_run_recovers_nothing(self, mocker, allocation_stage_stack):
        attach_job(allocation_stage_stack, 'vanished')
        fail = mocker.patch.object(request_handlers.AllocationRequestHandler, 'fail_lost_stage')

        call_command('observe_lost_jobs', '--dry-run')

        fail.assert_not_called()

    def test_it_exits_non_zero_when_a_recovery_fails(self, mocker, allocation_stage_stack):
        attach_job(allocation_stage_stack, 'vanished')
        mocker.patch.object(
            request_handlers.AllocationRequestHandler,
            'fail_lost_stage',
            side_effect=RuntimeError('boom'),
        )

        with pytest.raises(CommandError):
            call_command('observe_lost_jobs')

    def test_the_configuration_defaults_to_enabled_with_the_documented_grace(self):
        assert settings.CRCZP_CONFIG.lost_job_observer.enabled
        assert settings.CRCZP_CONFIG.lost_job_observer.grace_seconds == 900


class TestStopThatNeverLanded:
    """A stop sent by the automatic failure path leaves no person to finish it."""

    def test_a_stage_with_a_stale_stop_and_a_dead_job_is_lost(self, allocation_stage_stack):
        attach_job(allocation_stage_stack, 'vanished')
        AllocationStage.objects.filter(pk=allocation_stage_stack.pk).update(
            stop_requested_at=LONG_AGO
        )

        [work] = find_work()

        assert work.kind is LostWorkKind.ALLOCATION_STAGE

    def test_a_stage_with_a_stale_stop_and_a_living_job_is_not_lost(
        self, connection, allocation_stage_stack
    ):
        job = store_job(connection, JobStatus.STARTED, worker_name='alive')
        keep_worker_alive(connection, 'alive')
        attach_job(allocation_stage_stack, job.id)
        AllocationStage.objects.filter(pk=allocation_stage_stack.pk).update(
            stop_requested_at=LONG_AGO
        )

        assert not find_work()

    def test_the_recovery_fails_the_stage_and_queues_the_cleanup(
        self,
        mocker,
        allocation_stage_stack,
        allocation_stage_networking,
        allocation_stage_user,
    ):
        attach_job(allocation_stage_stack, 'vanished')
        AllocationStage.objects.filter(pk=allocation_stage_stack.pk).update(
            stop_requested_at=LONG_AGO
        )
        force = mocker.patch.object(requests, 'create_cleanup_request_force')
        [work] = find_work()

        assert not lost_jobs.recover_lost_work([work])

        allocation_stage_stack.refresh_from_db()
        assert allocation_stage_stack.finished
        force.assert_called_once()
