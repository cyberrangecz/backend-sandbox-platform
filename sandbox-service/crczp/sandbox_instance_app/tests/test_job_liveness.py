"""Tests for the liveness of the RQ job of a stage."""

from datetime import timedelta

import django_rq
import fakeredis
import pytest
from django.utils import timezone
from rq.job import Job, JobStatus
from rq.worker import Worker

from crczp.sandbox_instance_app.lib.job_liveness import (
    JobLiveness,
    get_stage_job_liveness,
    get_stages_job_liveness,
)
from crczp.sandbox_instance_app.models import (
    ENQUEUE_GRACE,
    AllocationRequest,
    AllocationRQJob,
    SandboxAllocationUnit,
    StackAllocationStage,
)

pytestmark = pytest.mark.django_db

WORKER_NAME = 'worker-one'


def noop() -> None:
    """Job body that is never executed."""


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """Serve every django_rq connection from one fresh in-memory Redis."""
    connection = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    monkeypatch.setattr(django_rq, 'get_connection', lambda *args, **kwargs: connection)
    return connection


@pytest.fixture
def stage_factory(allocation_unit):
    """Return a factory of stages, each with its own allocation request and job row."""

    def create(job_id: str | None, finished: bool = False) -> StackAllocationStage:
        unit = SandboxAllocationUnit.objects.create(pool=allocation_unit.pool)
        request = AllocationRequest.objects.create(allocation_unit=unit)
        stage = StackAllocationStage.objects.create(
            allocation_request=request, allocation_request_fk_many=request, finished=finished
        )
        if job_id is not None:
            AllocationRQJob.objects.create(job_id=job_id, allocation_stage=stage)
        return stage

    return create


def create_job(status: JobStatus, worker_name: str | None = None) -> Job:
    """Store a job with the given status and executing worker in Redis."""
    job = Job.create(noop, connection=django_rq.get_connection())
    job.worker_name = worker_name
    job.save()
    job.set_status(status)
    return job


def register_worker_heartbeat(name: str = WORKER_NAME, ttl: int = 420) -> None:
    """Create the worker key that a live worker keeps refreshing."""
    connection = django_rq.get_connection()
    key = Worker.redis_worker_namespace_prefix + name
    connection.hset(key, 'last_heartbeat', '2026-01-01T00:00:00Z')
    connection.expire(key, ttl)


@pytest.mark.parametrize('status', [JobStatus.QUEUED, JobStatus.DEFERRED, JobStatus.SCHEDULED])
def test_waiting_job_is_queued(stage_factory, status):
    """Queued, deferred and scheduled jobs read as queued."""
    stage = stage_factory(create_job(status).id)

    assert get_stage_job_liveness(stage) == JobLiveness.QUEUED


def test_started_job_with_live_worker_is_alive(stage_factory):
    """A started job reads as alive while its worker key exists."""
    register_worker_heartbeat()
    stage = stage_factory(create_job(JobStatus.STARTED, WORKER_NAME).id)

    assert get_stage_job_liveness(stage) == JobLiveness.ALIVE


def test_started_job_with_expired_worker_is_dead(stage_factory):
    """A started job whose worker key has expired reads as dead."""
    register_worker_heartbeat()
    django_rq.get_connection().delete(Worker.redis_worker_namespace_prefix + WORKER_NAME)
    stage = stage_factory(create_job(JobStatus.STARTED, WORKER_NAME).id)

    assert get_stage_job_liveness(stage) == JobLiveness.DEAD


def test_started_job_without_worker_is_dead(stage_factory):
    """A started job that names no worker reads as dead."""
    stage = stage_factory(create_job(JobStatus.STARTED).id)

    assert get_stage_job_liveness(stage) == JobLiveness.DEAD


@pytest.mark.parametrize(
    'status', [JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELED, JobStatus.FINISHED]
)
def test_ended_job_is_dead(stage_factory, status):
    """Failed, stopped, canceled and finished jobs of an unfinished stage read as dead."""
    stage = stage_factory(create_job(status).id)

    assert get_stage_job_liveness(stage) == JobLiveness.DEAD


def test_missing_job_row_is_dead(stage_factory):
    """A stage without a job row reads as dead."""
    assert get_stage_job_liveness(stage_factory(None)) == JobLiveness.DEAD


def test_expired_job_is_dead(stage_factory):
    """A job id that no longer exists in Redis reads as dead."""
    assert get_stage_job_liveness(stage_factory('expired-job-id')) == JobLiveness.DEAD


def test_many_stages_keep_their_order(stage_factory):
    """The batch form answers for each stage in the order given."""
    register_worker_heartbeat()
    stages = [
        stage_factory(create_job(JobStatus.QUEUED).id),
        stage_factory(create_job(JobStatus.STARTED, WORKER_NAME).id),
        stage_factory(None),
        stage_factory('expired-job-id'),
    ]

    assert get_stages_job_liveness(stages) == [
        JobLiveness.QUEUED,
        JobLiveness.ALIVE,
        JobLiveness.DEAD,
        JobLiveness.DEAD,
    ]


def test_no_stages_give_no_answers():
    """An empty input needs no Redis access and gives an empty list."""
    assert get_stages_job_liveness([]) == []


@pytest.mark.parametrize(
    ('requested_ago', 'expected'),
    [
        (timedelta(0), JobLiveness.QUEUED),
        (ENQUEUE_GRACE + timedelta(seconds=1), JobLiveness.DEAD),
    ],
    ids=['within the grace', 'past the grace'],
)
def test_stage_whose_job_is_yet_to_be_queued(stage_factory, requested_ago, expected):
    """A stage without a job row reads as queued until the enqueue grace has passed."""
    stage = stage_factory(None)
    stage.job_requested_at = timezone.now() - requested_ago

    assert get_stage_job_liveness(stage) == expected
