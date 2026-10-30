"""Liveness of the RQ job that executes a sandbox stage."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

import django_rq
from redis import Redis
from rq.job import Job, JobStatus
from rq.worker import Worker

from crczp.sandbox_instance_app.models import Stage

QUEUED_STATUSES = frozenset({JobStatus.QUEUED, JobStatus.DEFERRED, JobStatus.SCHEDULED})


class JobLiveness(StrEnum):
    """Whether the job of a stage is waiting, running on a live worker, or gone."""

    QUEUED = 'queued'
    ALIVE = 'alive'
    DEAD = 'dead'


@dataclass(frozen=True)
class JobReading:
    """The liveness of one RQ job together with the job as Redis holds it."""

    liveness: JobLiveness
    job: Job | None


def get_stage_job_liveness(stage: Stage) -> JobLiveness:
    """Return the liveness of the job of one stage."""
    return get_stages_job_liveness([stage])[0]


def get_stages_job_liveness(stages: Iterable[Stage]) -> list[JobLiveness]:
    """
    Return the liveness of the job of each stage, in the order given.

    A stage whose job is yet to be queued reads as queued. Any other stage without a job
    row, a job missing from Redis, a failed, stopped, canceled or finished job, and a
    started job whose worker has no unexpired heartbeat key all read as dead. Jobs and
    workers are read in one pipelined round trip each, whatever the number of stages.
    Stages should be loaded with ``select_related('rq_job')`` to avoid one query per stage.
    """
    stages = list(stages)
    liveness_per_job = get_jobs_liveness([_get_job_id(stage) for stage in stages])
    return [
        JobLiveness.QUEUED if stage.enqueue_pending else liveness
        for stage, liveness in zip(stages, liveness_per_job, strict=True)
    ]


def get_jobs_liveness(job_ids: Sequence[str | None]) -> list[JobLiveness]:
    """
    Return the liveness of each RQ job, in the order given.

    A missing job ID reads as dead; every other rule is that of ``get_stages_job_liveness``.
    """
    return [reading.liveness for reading in read_jobs(job_ids)]


def read_jobs(job_ids: Sequence[str | None]) -> list[JobReading]:
    """
    Return the liveness and the stored job of each RQ job ID, in the order given.

    The job is None where the ID is missing or Redis lost the job, which then reads as dead.
    """
    connection = django_rq.get_connection()
    jobs_by_id = _fetch_jobs(connection, {job_id for job_id in job_ids if job_id})
    worker_alive_by_name = _fetch_worker_presence(
        connection, {job.worker_name for job in jobs_by_id.values() if job.worker_name}
    )
    readings = []
    for job_id in job_ids:
        job = jobs_by_id.get(job_id) if job_id else None
        readings.append(JobReading(_classify(job, worker_alive_by_name), job))
    return readings


def _get_job_id(stage: Stage) -> str | None:
    job_row = getattr(stage, 'rq_job', None)
    return job_row.job_id if job_row else None


def _fetch_jobs(connection: Redis, job_ids: set[str]) -> dict[str, Job]:
    if not job_ids:
        return {}
    ordered_ids = sorted(job_ids)
    jobs = Job.fetch_many(ordered_ids, connection=connection)
    return {job_id: job for job_id, job in zip(ordered_ids, jobs, strict=True) if job is not None}


def _fetch_worker_presence(connection: Redis, worker_names: set[str]) -> dict[str, bool]:
    if not worker_names:
        return {}
    ordered_names = sorted(worker_names)
    pipeline = connection.pipeline()
    for name in ordered_names:
        pipeline.exists(Worker.redis_worker_namespace_prefix + name)
    return dict(zip(ordered_names, map(bool, pipeline.execute()), strict=True))


def _classify(job: Job | None, worker_alive_by_name: dict[str, bool]) -> JobLiveness:
    if job is None:
        return JobLiveness.DEAD
    status = job.get_status(refresh=False)
    if status in QUEUED_STATUSES:
        return JobLiveness.QUEUED
    if status == JobStatus.STARTED and worker_alive_by_name.get(job.worker_name or ''):
        return JobLiveness.ALIVE
    return JobLiveness.DEAD
