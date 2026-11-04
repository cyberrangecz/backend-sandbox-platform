"""
Tests of the RQ behaviour the sandbox service relies on, run against a real RQ queue.

Jobs are stored in an in-memory Redis (fakeredis) and executed by an RQ ``SimpleWorker`` in
burst mode, so queueing, dependencies, retries, callbacks, registries and the job-liveness
reads are the library's own, not mocks.
"""

import json
import signal
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import patch

import django_rq
import fakeredis
import pytest
from redis import Redis
from rq import Queue, Retry, SimpleWorker, Worker
from rq.command import (
    PUBSUB_CHANNEL_TEMPLATE,
    handle_command,
    handle_stop_job_command,
    send_stop_job_command,
)
from rq.exceptions import AbandonedJobError, InvalidJobOperation, NoSuchJobError
from rq.executions import Execution
from rq.job import Dependency, Job, JobStatus, get_current_job
from rq.registry import FailedJobRegistry, StartedJobRegistry
from rq.utils import current_timestamp
from rq.worker.base import BaseWorker

from crczp.sandbox_instance_app.lib.job_liveness import JobLiveness, get_jobs_liveness
from crczp.sandbox_instance_app.lib.stage_handlers import CancellableJob

FAILURE_CALLBACK_CALLS: list[tuple[str, str]] = []
EXCEPTION_HANDLER_CALLS: list[tuple[str, str]] = []
EXECUTION_LOG: list[str] = []
LIVENESS_SEEN_BY_JOB: list[JobLiveness] = []
ATTEMPTS: list[int] = []


def succeed() -> str:
    EXECUTION_LOG.append('succeed')
    return 'done'


def fail() -> None:
    EXECUTION_LOG.append('fail')
    raise RuntimeError('boom')


def run_named(name: str) -> str:
    EXECUTION_LOG.append(name)
    return name


def fail_once() -> str:
    ATTEMPTS.append(1)
    if len(ATTEMPTS) == 1:
        raise RuntimeError('first attempt fails')
    return 'recovered'


def read_own_liveness() -> None:
    current_job = get_current_job()
    assert current_job is not None
    LIVENESS_SEEN_BY_JOB.append(get_jobs_liveness([current_job.id])[0])


def record_failure(
    job: Job, _connection: Redis, exc_type: type, _exc_value: object, _traceback: object
) -> None:
    FAILURE_CALLBACK_CALLS.append((job.id, exc_type.__name__))


def record_exception_handler_call(
    job: Job, exc_type: type, _exc_value: object, _traceback: object
) -> None:
    EXCEPTION_HANDLER_CALLS.append((job.id, exc_type.__name__))


@pytest.fixture(autouse=True)
def reset_recordings() -> None:
    for recording in (
        FAILURE_CALLBACK_CALLS,
        EXCEPTION_HANDLER_CALLS,
        EXECUTION_LOG,
        LIVENESS_SEEN_BY_JOB,
        ATTEMPTS,
    ):
        recording.clear()


@pytest.fixture
def connection(monkeypatch) -> Redis:
    """One fresh in-memory Redis that every django_rq connection read resolves to."""
    fake_connection = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    monkeypatch.setattr(django_rq, 'get_connection', lambda *args, **kwargs: fake_connection)
    return fake_connection


@pytest.fixture
def queue(connection) -> Queue:
    return Queue('default', connection=connection)


def run_worker(queue: Queue, **worker_options) -> SimpleWorker:
    worker = SimpleWorker([queue], connection=queue.connection, **worker_options)
    worker.work(burst=True)
    return worker


def status_of(job: Job) -> JobStatus:
    return job.get_status(refresh=True)


def begin_job_on_worker(queue: Queue, worker_class: type[BaseWorker]) -> tuple[Job, BaseWorker]:
    """Let a worker of the given class take a queued job and begin it, with a failure callback."""
    queue.enqueue(succeed, on_failure=record_failure, retry=None)
    dequeued = Queue.dequeue_any([queue], timeout=None, connection=queue.connection)
    assert dequeued is not None
    job, _ = dequeued
    worker = worker_class([queue], connection=queue.connection)
    worker.register_birth()
    worker.prepare_job_execution(job)
    with queue.connection.pipeline() as pipeline:
        worker.execution = Execution.create(job, ttl=60, pipeline=pipeline, worker_name=worker.name)
        pipeline.execute()
    return job, worker


@pytest.fixture
def started_job_of_dead_worker(queue) -> Iterator[tuple[Job, SimpleWorker]]:
    """A job a worker took and began, with a failure callback, whose worker then died."""
    job, worker = begin_job_on_worker(queue, SimpleWorker)
    yield job, cast(SimpleWorker, worker)


@pytest.fixture
def started_job_of_forking_worker(queue) -> tuple[Job, Worker]:
    """A job a forking-class worker took and began; no horse process exists."""
    job, worker = begin_job_on_worker(queue, Worker)
    return job, cast(Worker, worker)


def simulate_worker_death(worker: SimpleWorker) -> None:
    worker.connection.delete(worker.key)


def collect_abandoned_jobs(queue: Queue, exception_handlers: list | None = None) -> None:
    """Run the registry maintenance a live worker runs, as if every job deadline had passed."""
    StartedJobRegistry(queue=queue).cleanup(
        timestamp=current_timestamp() + 10_000, exception_handlers=exception_handlers
    )


class TestDependencies:
    def test_dependent_runs_after_its_dependency_succeeded(self, queue):
        first = queue.enqueue(run_named, 'first')
        second = queue.enqueue(run_named, 'second', depends_on=first)
        assert status_of(second) == JobStatus.DEFERRED

        run_worker(queue)

        assert EXECUTION_LOG == ['first', 'second']
        assert status_of(second) == JobStatus.FINISHED

    def test_dependent_of_several_jobs_waits_for_all_of_them(self, queue):
        first = queue.enqueue(run_named, 'first')
        second = queue.enqueue(run_named, 'second')
        third = queue.enqueue(run_named, 'third', depends_on=[first, second])

        run_worker(queue)

        assert EXECUTION_LOG[-1] == 'third'
        assert status_of(third) == JobStatus.FINISHED

    def test_dependent_stays_deferred_when_its_dependency_fails(self, queue):
        failing = queue.enqueue(fail)
        dependent = queue.enqueue(run_named, 'dependent', depends_on=failing)

        run_worker(queue)

        assert status_of(failing) == JobStatus.FAILED
        assert status_of(dependent) == JobStatus.DEFERRED
        assert 'dependent' not in EXECUTION_LOG

    def test_allow_failure_runs_dependent_after_its_dependency_failed(self, queue):
        failing = queue.enqueue(fail)
        dependent = queue.enqueue(
            run_named, 'dependent', depends_on=Dependency(jobs=[failing], allow_failure=True)
        )

        run_worker(queue)

        assert status_of(failing) == JobStatus.FAILED
        assert status_of(dependent) == JobStatus.FINISHED

    def test_allow_failure_does_not_release_dependent_of_a_stopped_job(self, queue):
        stopped = queue.enqueue(succeed)
        dependent = queue.enqueue(
            run_named, 'dependent', depends_on=Dependency(jobs=[stopped], allow_failure=True)
        )
        stopped.set_status(JobStatus.STOPPED)

        queue.enqueue_dependents(stopped)

        assert status_of(dependent) == JobStatus.DEFERRED


class TestStopCommand:
    def test_stop_command_of_a_queued_job_is_refused(self, queue):
        job = queue.enqueue(succeed)

        with pytest.raises(InvalidJobOperation):
            send_stop_job_command(queue.connection, job.id)

    def test_stop_command_of_an_unknown_job_is_refused(self, queue):
        with pytest.raises(NoSuchJobError):
            send_stop_job_command(queue.connection, 'no-such-job')

    def test_stop_command_of_a_started_job_reaches_its_worker(self, started_job_of_dead_worker):
        job, worker = started_job_of_dead_worker
        pubsub = worker.connection.pubsub()
        pubsub.subscribe(PUBSUB_CHANNEL_TEMPLATE % worker.name)
        pubsub.get_message(timeout=1)

        send_stop_job_command(worker.connection, job.id)

        message = pubsub.get_message(timeout=1)
        assert json.loads(message['data']) == {'command': 'stop-job', 'job_id': job.id}

    def test_worker_that_runs_the_job_kills_its_horse_on_stop_command(
        self, started_job_of_dead_worker
    ):
        job, worker = started_job_of_dead_worker
        killed: list[bool] = []
        worker.kill_horse = lambda *args, **kwargs: killed.append(True)

        handle_stop_job_command(worker, {'command': 'stop-job', 'job_id': job.id})

        assert killed == [True]

    def test_worker_that_runs_the_job_kills_the_process_group_of_its_horse(
        self, started_job_of_forking_worker
    ):
        job, worker = started_job_of_forking_worker
        worker._horse_pid = 4242
        with (
            patch('rq.worker.worker_classes.os.getpgid', return_value=777) as get_group,
            patch('rq.worker.worker_classes.os.killpg') as kill_group,
        ):
            handle_stop_job_command(worker, {'command': 'stop-job', 'job_id': job.id})

        get_group.assert_called_once_with(4242)
        kill_group.assert_called_once_with(777, signal.SIGKILL)

    def test_worker_ignores_stop_command_of_a_job_it_does_not_run(self, started_job_of_dead_worker):
        _, worker = started_job_of_dead_worker
        killed: list[bool] = []
        worker.kill_horse = lambda *args, **kwargs: killed.append(True)

        handle_stop_job_command(worker, {'command': 'stop-job', 'job_id': 'another-job'})

        assert killed == []

    def test_stopped_job_ends_stopped_and_releases_no_dependent(
        self, queue, started_job_of_dead_worker
    ):
        job, worker = started_job_of_dead_worker
        dependent = queue.enqueue(
            run_named, 'dependent', depends_on=Dependency(jobs=[job], allow_failure=True)
        )
        worker._stopped_job_id = job.id

        worker.handle_job_failure(job, queue, exc_string='killed')

        assert status_of(job) == JobStatus.STOPPED
        assert status_of(dependent) == JobStatus.DEFERRED


class TestCancellableJob:
    """The production stop path (``CancellableJob._stop_job``) against a real queue."""

    class Handler(CancellableJob):
        def __init__(self, job_id: str | None) -> None:
            super().__init__()
            self._job_id = job_id
            self.stop_records = 0

        @property
        def job_id(self) -> str | None:
            return self._job_id

        @property
        def finished(self) -> bool:
            return False

        @property
        def enqueue_pending(self) -> bool:
            return False

        def refresh(self) -> None:
            """Nothing is stored beside the job."""

        def request_stop(self) -> None:
            self._stop_job()

        def mark_cancelled(self) -> None:
            """Nothing is stored beside the job."""

        def _record_stop_sent(self) -> None:
            self.stop_records += 1

    def test_queued_job_is_taken_off_the_queue_and_deleted_with_its_dependents(self, queue):
        job = queue.enqueue(succeed)
        dependent = queue.enqueue(run_named, 'dependent', depends_on=job)

        handler = self.Handler(job.id)
        handler.request_stop()

        assert job.id not in queue.job_ids
        assert not queue.connection.exists(job.key)
        assert not queue.connection.exists(dependent.key)
        assert handler.stop_sent is False

    def test_started_job_gets_the_stop_command_and_loses_its_dependents(
        self, queue, started_job_of_dead_worker
    ):
        job, worker = started_job_of_dead_worker
        dependent = queue.enqueue(run_named, 'dependent', depends_on=job)
        pubsub = worker.connection.pubsub()
        pubsub.subscribe(PUBSUB_CHANNEL_TEMPLATE % worker.name)
        pubsub.get_message(timeout=1)

        handler = self.Handler(job.id)
        handler.request_stop()

        message = pubsub.get_message(timeout=1)
        assert json.loads(message['data'])['job_id'] == job.id
        assert handler.stop_sent is True
        assert handler.stop_records == 1
        assert not queue.connection.exists(dependent.key)

    def test_stop_of_a_started_job_ends_in_the_kill_of_its_horse_process_group(
        self, started_job_of_forking_worker
    ):
        job, worker = started_job_of_forking_worker
        worker._horse_pid = 4242
        pubsub = worker.connection.pubsub()
        pubsub.subscribe(PUBSUB_CHANNEL_TEMPLATE % worker.name)
        pubsub.get_message(timeout=1)

        self.Handler(job.id).request_stop()

        message = pubsub.get_message(timeout=1)
        with (
            patch('rq.worker.worker_classes.os.getpgid', return_value=777),
            patch('rq.worker.worker_classes.os.killpg') as kill_group,
        ):
            handle_command(worker, json.loads(message['data']))
        kill_group.assert_called_once_with(777, signal.SIGKILL)

    def test_finished_job_is_deleted(self, queue):
        job = queue.enqueue(succeed)
        run_worker(queue)

        self.Handler(job.id).request_stop()

        assert not queue.connection.exists(job.key)

    def test_job_lost_from_redis_is_ignored(self, queue):
        handler = self.Handler('no-such-job')

        handler.request_stop()

        assert handler.stop_sent is False


class TestDeleteDependents:
    def test_delete_with_dependents_removes_the_whole_chain(self, queue):
        first = queue.enqueue(run_named, 'first')
        second = queue.enqueue(run_named, 'second', depends_on=first)
        third = queue.enqueue(run_named, 'third', depends_on=second)

        first.delete(delete_dependents=True)

        assert not queue.connection.exists(first.key)
        assert not queue.connection.exists(second.key)
        assert queue.connection.exists(third.key)
        second_job_ids = queue.deferred_job_registry.get_job_ids()
        assert second.id not in second_job_ids

    def test_deleted_dependent_never_runs(self, queue):
        first = queue.enqueue(run_named, 'first')
        queue.enqueue(run_named, 'second', depends_on=first)
        first.delete_dependents()

        run_worker(queue)

        assert EXECUTION_LOG == ['first']

    def test_delete_of_a_started_job_with_dependents_removes_the_dependents(
        self, queue, started_job_of_dead_worker
    ):
        job, _ = started_job_of_dead_worker
        dependent = queue.enqueue(run_named, 'dependent', depends_on=job)

        job.delete(delete_dependents=True)

        assert not queue.connection.exists(dependent.key)


class TestRetry:
    def test_failed_job_is_run_again_until_it_succeeds(self, queue):
        job = queue.enqueue(fail_once, retry=Retry(max=2))

        run_worker(queue)

        assert len(ATTEMPTS) == 2
        assert status_of(job) == JobStatus.FINISHED

    def test_job_fails_for_good_once_its_retries_are_spent(self, queue):
        job = queue.enqueue(fail, retry=Retry(max=1))

        run_worker(queue)

        assert EXECUTION_LOG == ['fail', 'fail']
        assert status_of(job) == JobStatus.FAILED

    def test_dependent_is_not_released_while_the_dependency_is_retried(self, queue):
        job = queue.enqueue(fail_once, retry=Retry(max=1))
        dependent = queue.enqueue(run_named, 'dependent', depends_on=job)

        run_worker(queue)

        assert status_of(job) == JobStatus.FINISHED
        assert status_of(dependent) == JobStatus.FINISHED
        assert EXECUTION_LOG == ['dependent']


class TestFailureHandlers:
    def test_failure_callback_runs_when_the_job_raises(self, queue):
        job = queue.enqueue(fail, on_failure=record_failure)

        run_worker(queue)

        assert [(job.id, 'RuntimeError')] == FAILURE_CALLBACK_CALLS

    def test_failure_callback_does_not_run_on_success(self, queue):
        queue.enqueue(succeed, on_failure=record_failure)

        run_worker(queue)

        assert FAILURE_CALLBACK_CALLS == []

    def test_failure_callback_runs_for_every_failed_attempt_including_retried_ones(self, queue):
        job = queue.enqueue(fail_once, on_failure=record_failure, retry=Retry(max=1))

        run_worker(queue)

        assert status_of(job) == JobStatus.FINISHED
        assert [(job.id, 'RuntimeError')] == FAILURE_CALLBACK_CALLS

    def test_exception_handler_of_the_worker_runs_when_the_job_raises(self, queue):
        job = queue.enqueue(fail)

        run_worker(queue, exception_handlers=[record_exception_handler_call])

        assert [(job.id, 'RuntimeError')] == EXCEPTION_HANDLER_CALLS


class TestAbandonedJob:
    """A job whose worker died is collected by registry maintenance."""

    def test_abandoned_job_moves_to_the_failed_registry(self, queue, started_job_of_dead_worker):
        job, worker = started_job_of_dead_worker
        simulate_worker_death(worker)

        collect_abandoned_jobs(queue)

        assert status_of(job) == JobStatus.FAILED
        assert job.id in FailedJobRegistry(queue=queue).get_job_ids()
        assert job.id not in StartedJobRegistry(queue=queue).get_job_ids(cleanup=False)

    def test_abandoned_job_runs_its_failure_callback(self, queue, started_job_of_dead_worker):
        job, worker = started_job_of_dead_worker
        simulate_worker_death(worker)

        collect_abandoned_jobs(queue)

        assert [(job.id, AbandonedJobError.__name__)] == FAILURE_CALLBACK_CALLS

    def test_abandoned_job_runs_the_exception_handlers_of_the_worker(
        self, queue, started_job_of_dead_worker
    ):
        job, worker = started_job_of_dead_worker
        simulate_worker_death(worker)

        collect_abandoned_jobs(queue, exception_handlers=[record_exception_handler_call])

        assert [(job.id, AbandonedJobError.__name__)] == EXCEPTION_HANDLER_CALLS

    def test_abandoned_job_leaves_a_plain_dependent_deferred(
        self, queue, started_job_of_dead_worker
    ):
        job, worker = started_job_of_dead_worker
        dependent = queue.enqueue(run_named, 'dependent', depends_on=job)
        simulate_worker_death(worker)

        collect_abandoned_jobs(queue)

        assert status_of(dependent) == JobStatus.DEFERRED

    def test_abandoned_job_releases_a_dependent_that_allows_failure(
        self, queue, started_job_of_dead_worker
    ):
        job, worker = started_job_of_dead_worker
        dependent = queue.enqueue(
            run_named, 'dependent', depends_on=Dependency(jobs=[job], allow_failure=True)
        )
        simulate_worker_death(worker)

        collect_abandoned_jobs(queue)
        run_worker(queue)

        assert status_of(dependent) == JobStatus.FINISHED

    def test_abandoned_job_with_retries_left_is_queued_again(
        self, queue, started_job_of_dead_worker
    ):
        job, worker = started_job_of_dead_worker
        job.retries_left = 1
        job.save()
        simulate_worker_death(worker)

        collect_abandoned_jobs(queue)

        assert status_of(job) == JobStatus.QUEUED
        assert job.id in queue.job_ids

    def test_job_of_a_live_worker_is_not_collected(self, queue, started_job_of_dead_worker):
        job, _ = started_job_of_dead_worker

        StartedJobRegistry(queue=queue).cleanup()

        assert status_of(job) == JobStatus.STARTED
        assert FAILURE_CALLBACK_CALLS == []


class TestJobLiveness:
    """Every outcome of the job-liveness reads, against jobs a real RQ stores."""

    def test_queued_job_reads_queued(self, queue):
        job = queue.enqueue(succeed)

        assert get_jobs_liveness([job.id]) == [JobLiveness.QUEUED]

    def test_deferred_job_reads_queued(self, queue):
        first = queue.enqueue(succeed)
        deferred = queue.enqueue(succeed, depends_on=first)

        assert get_jobs_liveness([deferred.id]) == [JobLiveness.QUEUED]

    def test_scheduled_job_reads_queued(self, queue):
        job = queue.enqueue_at(datetime.now(UTC) + timedelta(hours=1), succeed)

        assert get_jobs_liveness([job.id]) == [JobLiveness.QUEUED]

    def test_job_running_on_a_worker_reads_alive_from_inside_the_job(self, queue):
        queue.enqueue(read_own_liveness)

        run_worker(queue)

        assert LIVENESS_SEEN_BY_JOB == [JobLiveness.ALIVE]

    def test_started_job_of_a_live_worker_reads_alive(self, started_job_of_dead_worker):
        job, _ = started_job_of_dead_worker

        assert get_jobs_liveness([job.id]) == [JobLiveness.ALIVE]

    def test_started_job_whose_worker_key_expired_reads_dead(self, started_job_of_dead_worker):
        job, worker = started_job_of_dead_worker
        simulate_worker_death(worker)

        assert get_jobs_liveness([job.id]) == [JobLiveness.DEAD]

    def test_started_job_without_a_worker_name_reads_dead(self, queue):
        job = queue.enqueue(succeed)
        job.set_status(JobStatus.STARTED)

        assert get_jobs_liveness([job.id]) == [JobLiveness.DEAD]

    def test_finished_job_reads_dead(self, queue):
        job = queue.enqueue(succeed)
        run_worker(queue)

        assert get_jobs_liveness([job.id]) == [JobLiveness.DEAD]

    def test_failed_job_reads_dead(self, queue):
        job = queue.enqueue(fail)
        run_worker(queue)

        assert get_jobs_liveness([job.id]) == [JobLiveness.DEAD]

    def test_stopped_job_reads_dead(self, queue):
        job = queue.enqueue(succeed)
        job.set_status(JobStatus.STOPPED)

        assert get_jobs_liveness([job.id]) == [JobLiveness.DEAD]

    def test_canceled_job_reads_dead(self, queue):
        job = queue.enqueue(succeed)
        job.cancel()

        assert get_jobs_liveness([job.id]) == [JobLiveness.DEAD]

    def test_abandoned_job_reads_dead(self, queue, started_job_of_dead_worker):
        job, worker = started_job_of_dead_worker
        simulate_worker_death(worker)
        collect_abandoned_jobs(queue)

        assert get_jobs_liveness([job.id]) == [JobLiveness.DEAD]

    def test_job_missing_from_redis_and_missing_id_read_dead(self, queue):
        assert get_jobs_liveness(['no-such-job', None]) == [JobLiveness.DEAD, JobLiveness.DEAD]

    def test_several_jobs_are_read_in_the_order_given(self, queue, started_job_of_dead_worker):
        started, _ = started_job_of_dead_worker
        queued = queue.enqueue(succeed)

        assert get_jobs_liveness([queued.id, None, started.id]) == [
            JobLiveness.QUEUED,
            JobLiveness.DEAD,
            JobLiveness.ALIVE,
        ]
