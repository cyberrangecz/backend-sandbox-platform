"""
Tests of an allocation whose worker died, run against a real RQ queue.

The jobs of a real allocation request are stored in an in-memory Redis (fakeredis); the worker
death and the registry maintenance that collects the abandoned job are those of RQ itself.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name
from collections.abc import Iterator

import fakeredis
import pytest
from rq import Queue, SimpleWorker
from rq.exceptions import AbandonedJobError
from rq.executions import Execution
from rq.job import Job, JobStatus
from rq.registry import StartedJobRegistry
from rq.utils import current_timestamp

from crczp.sandbox_instance_app.lib import netbird, request_handlers, stage_handlers
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    CleanupRequest,
    SandboxRequestGroup,
)

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def connection(monkeypatch) -> Iterator[fakeredis.FakeRedis]:
    """One in-memory Redis that every django_rq connection read resolves to."""
    fake_connection = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    monkeypatch.setattr('django_rq.get_connection', lambda *args, **kwargs: fake_connection)
    yield fake_connection


@pytest.fixture
def queues(connection) -> dict[str, Queue]:
    return {
        name: Queue(name, connection=connection) for name in ('default', 'openstack', 'ansible')
    }


@pytest.fixture
def enqueued_allocation(
    mocker,
    queues,
    sandbox,
    allocation_request,
    allocation_stage_stack,
    allocation_stage_networking,
    allocation_stage_user,
):
    """An allocation request whose three stage jobs and NetBird job are queued for real."""
    mocker.patch.object(netbird, 'is_netbird_configured', return_value=True)
    mocker.patch('crczp.sandbox_instance_app.lib.request_handlers.LOG')
    handler = request_handlers.AllocationRequestHandler()
    handler.queue_default = queues['default']
    handler.queue_stack = queues['openstack']
    handler.queue_ansible = queues['ansible']
    handlers: list[stage_handlers.StageHandler] = [
        stage_handlers.AllocationStackStageHandler(allocation_stage_stack),
        stage_handlers.AllocationAnsibleStageHandler(allocation_stage_networking, sandbox),
        stage_handlers.AllocationAnsibleStageHandler(allocation_stage_user, sandbox),
    ]
    handler._enqueue_allocation_request(sandbox, handlers, allocation_request)
    allocation_request.refresh_from_db()
    return allocation_request


def start_on_dead_worker(queue: Queue, job_id: str) -> None:
    """Let a worker take the queued job and begin it, then kill the worker."""
    job = Job.fetch(job_id, connection=queue.connection)
    if job.is_deferred:
        job.set_status(JobStatus.QUEUED)
        queue.push_job_id(job_id)
    assert queue.remove(job_id)
    worker = SimpleWorker([queue], connection=queue.connection)
    worker.register_birth()
    worker.prepare_job_execution(job)
    with queue.connection.pipeline() as pipeline:
        worker.execution = Execution.create(job, ttl=60, pipeline=pipeline, worker_name=worker.name)
        pipeline.execute()
    worker.connection.delete(worker.key)


def collect_abandoned_jobs(queue: Queue) -> None:
    StartedJobRegistry(queue=queue).cleanup(timestamp=current_timestamp() + 10_000)


def stage_job_id(request: AllocationRequest, stage_name: str) -> str:
    return getattr(request, stage_name).rq_job.job_id


class TestAbandonedAllocationJob:
    def test_the_jobs_are_queued_with_the_failure_callback(self, enqueued_allocation, queues):
        job = Job.fetch(
            stage_job_id(enqueued_allocation, 'stackallocationstage'),
            connection=queues['default'].connection,
        )

        assert job.failure_callback is request_handlers.fail_abandoned_allocation_job
        netbird_job = Job.fetch(
            enqueued_allocation.netbird_job_id, connection=queues['default'].connection
        )
        assert netbird_job.failure_callback is request_handlers.fail_abandoned_allocation_job

    def test_an_abandoned_stack_stage_fails_with_the_reason_of_the_dead_worker(
        self, enqueued_allocation, queues
    ):
        stack_job_id = stage_job_id(enqueued_allocation, 'stackallocationstage')
        start_on_dead_worker(queues['openstack'], stack_job_id)

        collect_abandoned_jobs(queues['openstack'])

        stack_stage = AllocationRequest.objects.get(pk=enqueued_allocation.pk).stackallocationstage
        assert stack_stage.failed
        assert stack_stage.finished
        assert stack_stage.error_message == stage_handlers.WORKER_DIED_MESSAGE

    def test_the_dependents_of_the_abandoned_job_do_not_stay_deferred(
        self, enqueued_allocation, queues
    ):
        networking_job_id = stage_job_id(enqueued_allocation, 'networkingansibleallocationstage')
        user_job_id = stage_job_id(enqueued_allocation, 'useransibleallocationstage')
        netbird_job_id = enqueued_allocation.netbird_job_id
        start_on_dead_worker(
            queues['openstack'], stage_job_id(enqueued_allocation, 'stackallocationstage')
        )

        collect_abandoned_jobs(queues['openstack'])

        for job_id in (networking_job_id, user_job_id, netbird_job_id):
            assert not Job.exists(job_id, connection=queues['default'].connection)
        refreshed = AllocationRequest.objects.get(pk=enqueued_allocation.pk)
        assert refreshed.is_finished
        assert refreshed.netbird_job_id is None
        assert all(stage.failed for stage in refreshed.stages.all())

    def test_the_cleanup_is_queued(self, enqueued_allocation, queues):
        start_on_dead_worker(
            queues['openstack'], stage_job_id(enqueued_allocation, 'stackallocationstage')
        )

        collect_abandoned_jobs(queues['openstack'])

        assert CleanupRequest.objects.filter(
            allocation_unit=enqueued_allocation.allocation_unit
        ).exists()

    def test_the_queued_cleanup_creation_job_builds_the_cleanup_stages_after_the_commit(
        self, monkeypatch, enqueued_allocation, queues, django_capture_on_commit_callbacks
    ):
        monkeypatch.setattr(request_handlers.RequestHandler, 'queue_default', queues['default'])
        monkeypatch.setattr(request_handlers.RequestHandler, 'queue_stack', queues['openstack'])
        monkeypatch.setattr(request_handlers.RequestHandler, 'queue_ansible', queues['ansible'])
        start_on_dead_worker(
            queues['openstack'], stage_job_id(enqueued_allocation, 'stackallocationstage')
        )
        queued_before = set(queues['default'].job_ids)

        with django_capture_on_commit_callbacks(execute=True):
            collect_abandoned_jobs(queues['openstack'])

        creation_job_ids = set(queues['default'].job_ids) - queued_before
        assert len(creation_job_ids) == 1
        cleanup_request = CleanupRequest.objects.get(
            allocation_unit=enqueued_allocation.allocation_unit
        )
        assert not cleanup_request.stages.exists()

        with django_capture_on_commit_callbacks(execute=True):
            SimpleWorker([queues['default']], connection=queues['default'].connection).work(
                burst=True
            )

        assert Job.fetch(
            creation_job_ids.pop(), connection=queues['default'].connection
        ).is_finished
        assert cleanup_request.stages.count() == 3
        assert all(stage.rq_job is not None for stage in cleanup_request.stages.all())

    def test_the_build_failure_is_counted_in_the_request_group(
        self, mocker, enqueued_allocation, queues, pool
    ):
        mocker.patch('crczp.sandbox_instance_app.models.send_email')
        group = SandboxRequestGroup.objects.create(pool=pool, unit_count=2, email='a@b.cz')
        AllocationRequest.objects.filter(pk=enqueued_allocation.pk).update(request_group=group)
        start_on_dead_worker(
            queues['openstack'], stage_job_id(enqueued_allocation, 'stackallocationstage')
        )

        collect_abandoned_jobs(queues['openstack'])

        group.refresh_from_db()
        assert group.failed_count == 1
        assert group.finished_count == 1

    def test_an_abandoned_ansible_stage_releases_the_user_stage_that_also_awaits_netbird(
        self, enqueued_allocation, queues
    ):
        stack_job = Job.fetch(
            stage_job_id(enqueued_allocation, 'stackallocationstage'),
            connection=queues['default'].connection,
        )
        stack_job.set_status(JobStatus.FINISHED)
        user_job_id = stage_job_id(enqueued_allocation, 'useransibleallocationstage')
        start_on_dead_worker(
            queues['ansible'], stage_job_id(enqueued_allocation, 'networkingansibleallocationstage')
        )

        collect_abandoned_jobs(queues['ansible'])

        assert not Job.exists(user_job_id, connection=queues['default'].connection)
        refreshed = AllocationRequest.objects.get(pk=enqueued_allocation.pk)
        assert refreshed.networkingansibleallocationstage.error_message == (
            stage_handlers.WORKER_DIED_MESSAGE
        )

    def test_an_abandoned_netbird_job_fails_the_allocation(self, enqueued_allocation, queues):
        netbird_job_id = enqueued_allocation.netbird_job_id
        user_job_id = stage_job_id(enqueued_allocation, 'useransibleallocationstage')
        start_on_dead_worker(queues['ansible'], netbird_job_id)

        collect_abandoned_jobs(queues['ansible'])

        assert not Job.exists(user_job_id, connection=queues['default'].connection)
        refreshed = AllocationRequest.objects.get(pk=enqueued_allocation.pk)
        assert refreshed.is_finished
        assert refreshed.netbird_job_id is None
        assert CleanupRequest.objects.filter(allocation_unit=refreshed.allocation_unit).exists()

    def test_an_ordinary_failure_is_left_to_the_exception_handler(
        self, enqueued_allocation, queues
    ):
        job = Job.fetch(
            stage_job_id(enqueued_allocation, 'stackallocationstage'),
            connection=queues['default'].connection,
        )

        request_handlers.fail_abandoned_allocation_job(
            job, None, RuntimeError, RuntimeError(), None
        )

        refreshed = AllocationRequest.objects.get(pk=enqueued_allocation.pk)
        assert not refreshed.is_finished
        assert not CleanupRequest.objects.filter(allocation_unit=refreshed.allocation_unit).exists()

    def test_the_callback_of_an_abandoned_job_without_a_request_does_not_raise(self, queues):
        job = queues['default'].enqueue(print, 'unrelated')

        request_handlers.fail_abandoned_allocation_job(
            job, None, AbandonedJobError, AbandonedJobError(), None
        )
