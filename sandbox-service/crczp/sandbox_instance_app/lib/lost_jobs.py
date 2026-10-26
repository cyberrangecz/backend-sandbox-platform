"""
Detection of sandbox work whose RQ job was lost, and its recovery through the failure path.

Work is lost when no process is left that could react to its end: its job vanished from Redis,
its worker died and RQ never collected the job, or the job that creates the stages of a
request vanished before it ran.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import partial
from typing import Any

import structlog
from django.db.models import Manager
from django.utils import timezone
from rq.job import Job, JobStatus

from crczp.sandbox_ansible_app.models import UserAnsibleAllocationStage
from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_instance_app.lib import allocation_cancellation, requests
from crczp.sandbox_instance_app.lib.job_liveness import JobLiveness, JobReading, read_jobs
from crczp.sandbox_instance_app.lib.request_handlers import (
    AllocationRequestHandler,
    CleanupRequestHandler,
    RequestHandler,
)
from crczp.sandbox_instance_app.lib.stage_handlers import (
    ABANDONED_STAGE_MESSAGES,
    JOB_LOST_MESSAGE,
    NETBIRD_JOB_LOST_MESSAGE,
    PRECEDING_STAGE_FAILED_MESSAGE,
    WORKER_DIED_MESSAGE,
)
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    AllocationStage,
    CleanupRequest,
    CleanupStage,
)

LOG = structlog.get_logger()

CLEANUP_CREATION_JOB_NAME = '_create_cleanup_jobs'


class LostWorkKind(StrEnum):
    """What was lost."""

    ALLOCATION_STAGE = 'allocation_stage'
    CLEANUP_STAGE = 'cleanup_stage'
    ALLOCATION_CREATION = 'allocation_creation'
    CLEANUP_CREATION = 'cleanup_creation'
    ABANDONED_ALLOCATION = 'abandoned_allocation'


@dataclass(frozen=True)
class LostWork:
    """
    One piece of lost work and the recovery that hands it to the failure path.

    :param kind: What was lost.
    :param unit_id: The allocation unit the work belongs to.
    :param reason: Why the work counts as lost.
    :param recover: Hands the work to the failure path.
    """

    kind: LostWorkKind
    unit_id: int
    reason: str
    recover: Callable[[], None] = field(compare=False, repr=False)


@dataclass(frozen=True)
class _PendingCreations:
    """The units whose stage-creation job is queued, scheduled or running."""

    allocation_unit_ids: set[int]
    cleanup_unit_ids: set[int]


def find_lost_work(grace: timedelta) -> list[LostWork]:
    """
    Find the work whose job is lost.

    A request is judged by its first unfinished stage, as the later ones wait behind it. A job
    still queued, or running on a living worker, is never lost. A job that Redis lost counts at
    once; a dead job it still holds counts once its last sign of life is older than the grace,
    which leaves RQ the time to collect an abandoned job and run its failure callback first.
    A stage whose stop was requested is left to that Cancel, until the stop is older than the
    grace and the job is dead: the stop never landed, and no one is left to finish the stage.
    A cleanup stage whose job waits behind a stage that failed longer ago than the grace counts
    as lost too.

    :param grace: How long a dead job that Redis still holds is given before it counts as lost.
    :return: The lost work, with the recovery of each.
    """
    now = timezone.now()
    return [
        *_find_lost_allocation_stages(now, grace),
        *_find_lost_cleanup_stages(now, grace),
        *_find_lost_creations(now, grace),
        *_find_abandoned_allocations(now, grace),
    ]


def recover_lost_work(lost_work: list[LostWork], *, dry_run: bool = False) -> list[LostWork]:
    """
    Hand each piece of lost work to the failure path, logging each action. A failing recovery
    does not stop the others.

    :param lost_work: The work to recover.
    :param dry_run: Only log what would be done.
    :return: The work whose recovery failed.
    """
    failed = []
    for work in lost_work:
        context = {'kind': work.kind, 'unit_id': work.unit_id, 'reason': work.reason}
        if dry_run:
            LOG.info('lost_work_would_be_recovered', **context)
            continue
        try:
            work.recover()
        except Exception:  # pylint: disable=broad-exception-caught
            LOG.exception('lost_work_recovery_failed', **context)
            failed.append(work)
        else:
            LOG.info('lost_work_recovered', **context)
    return failed


def _find_lost_allocation_stages(now: datetime, grace: timedelta) -> list[LostWork]:
    heads = _get_head_stages(
        AllocationStage.objects, 'allocation_request_fk_many', stale_stop_before=now - grace
    )
    readings = read_jobs([_get_job_id(stage) for stage in heads])
    lost_work = []
    for stage, reading in zip(heads, readings, strict=True):
        request: AllocationRequest = stage.allocation_request_fk_many
        reason = _get_lost_reason(reading, now, grace)
        if reason is None and _awaits_lost_netbird_job(stage, request, reading, now, grace):
            reason = NETBIRD_JOB_LOST_MESSAGE
        if reason is not None:
            lost_work.append(
                LostWork(
                    LostWorkKind.ALLOCATION_STAGE,
                    request.allocation_unit.pk,
                    reason,
                    partial(
                        AllocationRequestHandler().fail_lost_stage,
                        request,
                        stage.pk,
                        reason,
                        stop_never_landed=stage.stop_requested_at is not None,
                    ),
                )
            )
    return lost_work


def _find_lost_cleanup_stages(now: datetime, grace: timedelta) -> list[LostWork]:
    heads = _get_head_stages(CleanupStage.objects, 'cleanup_request_fk_many')
    readings = read_jobs([_get_job_id(stage) for stage in heads])
    lost_work = []
    for stage, reading in zip(heads, readings, strict=True):
        request: CleanupRequest = stage.cleanup_request_fk_many
        reason = _get_lost_reason(reading, now, grace)
        if reason is None and _follows_failed_stage(
            CleanupStage.objects, 'cleanup_request_fk_many', stage, now, grace
        ):
            reason = PRECEDING_STAGE_FAILED_MESSAGE
        if reason is not None:
            lost_work.append(
                LostWork(
                    LostWorkKind.CLEANUP_STAGE,
                    request.allocation_unit.pk,
                    reason,
                    partial(CleanupRequestHandler().fail_lost_stage, request, stage.pk, reason),
                )
            )
    return lost_work


def _follows_failed_stage(
    stages: Manager[Any], request_field: str, stage: Any, now: datetime, grace: timedelta
) -> bool:
    """
    Whether an earlier stage of the same request failed longer ago than the grace, which
    leaves the job of this stage waiting for a dependency that will never finish.
    """
    return stages.filter(
        **{request_field: getattr(stage, f'{request_field}_id')},
        id__lt=stage.pk,
        failed=True,
        finished=True,
        end__lt=now - grace,
    ).exists()


def _get_head_stages(
    stages: Manager[Any], request_field: str, stale_stop_before: datetime | None = None
) -> list[Any]:
    """
    Get the first unfinished stage of every request, leaving out a stage whose job may still be
    queued and one whose stop was requested, unless that stop was requested before
    stale_stop_before. The stages of a request are created in the order they run.
    """
    unfinished = stages.filter(finished=False).select_related(
        'rq_job', f'{request_field}__allocation_unit'
    )
    heads: dict[int, Any] = {}
    for stage in unfinished.order_by('id'):
        heads.setdefault(getattr(stage, f'{request_field}_id'), stage)
    return [
        stage
        for stage in heads.values()
        if (
            stage.stop_requested_at is None
            or (stale_stop_before is not None and stage.stop_requested_at < stale_stop_before)
        )
        and not stage.within_enqueue_grace
    ]


def _get_job_id(stage: Any) -> str | None:
    job_row = getattr(stage, 'rq_job', None)
    return job_row.job_id if job_row else None


def _get_lost_reason(reading: JobReading, now: datetime, grace: timedelta) -> str | None:
    """Why the job of a stage counts as lost, or None while it does not."""
    if reading.liveness is not JobLiveness.DEAD:
        return None
    if reading.job is None:
        return JOB_LOST_MESSAGE
    if not _is_dead_past_grace(reading.job, now, grace):
        return None
    if reading.job.get_status(refresh=False) == JobStatus.STARTED:
        return WORKER_DIED_MESSAGE
    return JOB_LOST_MESSAGE


def _is_dead_past_grace(job: Job, now: datetime, grace: timedelta) -> bool:
    """Whether the last sign of life of a dead job is older than the grace."""
    moments = [
        moment
        for moment in (
            job.last_heartbeat,
            job.ended_at,
            job.started_at,
            job.enqueued_at,
            job.created_at,
        )
        if moment is not None
    ]
    last_activity = max(
        moment if moment.tzinfo else moment.replace(tzinfo=UTC) for moment in moments
    )
    return now - last_activity >= grace


def _awaits_lost_netbird_job(
    stage: AllocationStage,
    request: AllocationRequest,
    reading: JobReading,
    now: datetime,
    grace: timedelta,
) -> bool:
    """
    Whether the user stage waits for a NetBird provisioning job that died unfinished.

    A NetBird job that Redis no longer holds, or that finished, counts as done for the jobs
    waiting for it, so only a job that is dead and still held is a reason to fail.
    """
    if (
        request.netbird_job_id is None
        or reading.job is None
        or reading.job.get_status(refresh=False) != JobStatus.DEFERRED
        or not UserAnsibleAllocationStage.objects.filter(pk=stage.pk).exists()
    ):
        return False
    [netbird_reading] = read_jobs([request.netbird_job_id])
    return (
        netbird_reading.liveness is JobLiveness.DEAD
        and netbird_reading.job is not None
        and netbird_reading.job.get_status(refresh=False) != JobStatus.FINISHED
        and _is_dead_past_grace(netbird_reading.job, now, grace)
    )


def _find_lost_creations(now: datetime, grace: timedelta) -> list[LostWork]:
    """
    Find the requests without stages whose stage-creation job is neither queued nor running.

    Nothing is reported when a queued creation job cannot be read.
    """
    pending = _read_pending_creations()
    if pending is None:
        return []
    cutoff = now - grace
    allocations = (
        AllocationRequest.objects
        .filter(
            stages__isnull=True, created__lt=cutoff, allocation_unit__cleanup_request__isnull=True
        )
        .exclude(allocation_unit_id__in=pending.allocation_unit_ids)
        .select_related('allocation_unit__pool')
    )
    cleanups = (
        CleanupRequest.objects
        .filter(stages__isnull=True, created__lt=cutoff)
        .exclude(allocation_unit_id__in=pending.cleanup_unit_ids)
        .select_related('allocation_unit__pool')
    )
    return [
        *(
            LostWork(
                LostWorkKind.ALLOCATION_CREATION,
                request.allocation_unit.pk,
                'The job that creates the stages of the allocation was lost.',
                partial(_remove_unbuilt_unit, request),
            )
            for request in allocations
        ),
        *(
            LostWork(
                LostWorkKind.CLEANUP_CREATION,
                request.allocation_unit.pk,
                'The job that creates the stages of the cleanup was lost.',
                partial(CleanupRequestHandler().fail_lost_creation, request, JOB_LOST_MESSAGE),
            )
            for request in cleanups
        ),
    ]


def _read_pending_creations() -> _PendingCreations | None:
    """
    Read the units whose stage-creation job is queued, scheduled or started, or None when
    such a job cannot be read.
    """
    queue = RequestHandler.queue_default
    job_ids = [
        *queue.job_ids,
        *queue.scheduled_job_registry.get_job_ids(),
        *queue.started_job_registry.get_job_ids(),
    ]
    pending = _PendingCreations(set(), set())
    for job in Job.fetch_many(job_ids, connection=queue.connection):
        if job is None:
            continue
        try:
            if job.func_name == allocation_cancellation.CREATION_JOB_NAME:
                pending.allocation_unit_ids.update(unit.id for unit in job.args[0])
            elif job.func_name == CLEANUP_CREATION_JOB_NAME:
                pending.cleanup_unit_ids.add(job.args[0].id)
        except Exception:  # pylint: disable=broad-exception-caught
            LOG.exception('creation_job_unreadable', job_id=job.id)
            return None
    return pending


def _remove_unbuilt_unit(request: AllocationRequest) -> None:
    """
    Remove the unit of an allocation that was never built, the way Cancel removes a queued one.

    :raises ValidationError: The unit could not be removed.
    """
    for result in allocation_cancellation.cancel_units(
        [request.allocation_unit], [], wait_seconds=0
    ):
        if result.action is allocation_cancellation.CancellationAction.FAILED:
            raise exceptions.ValidationError(result.reason)


def _find_abandoned_allocations(now: datetime, grace: timedelta) -> list[LostWork]:
    """
    Find the allocations that failed with an abandoned job as the reason and got no cleanup,
    because it was refused when the failure was handled.

    An allocation whose NetBird job may still run is left for a later run.
    """
    stages = AllocationStage.objects.filter(
        failed=True,
        error_message__in=ABANDONED_STAGE_MESSAGES,
        end__lt=now - grace,
        allocation_request_fk_many__allocation_unit__cleanup_request__isnull=True,
    ).select_related('allocation_request_fk_many__allocation_unit__pool')
    requests_by_id = {stage.allocation_request_fk_many.pk: stage for stage in stages}
    candidates = [
        (stage.allocation_request_fk_many, stage.error_message or '')
        for stage in requests_by_id.values()
        if stage.allocation_request_fk_many.is_finished
    ]
    readings = read_jobs([request.netbird_job_id for request, _ in candidates])
    return [
        LostWork(
            LostWorkKind.ABANDONED_ALLOCATION,
            request.allocation_unit.pk,
            f'The allocation failed ({reason}) and no cleanup was queued.',
            partial(
                requests.create_cleanup_request_force, request.allocation_unit, delete_pool=False
            ),
        )
        for (request, reason), reading in zip(candidates, readings, strict=True)
        if request.netbird_job_id is None or reading.liveness is JobLiveness.DEAD
    ]
