"""Cancellation of a pool's allocations: queued units are removed, running builds are stopped."""

import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

import structlog
from django.db import transaction
from django.db.models import Exists, OuterRef, QuerySet
from rq.job import JobStatus

from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_instance_app.lib import request_handlers, requests
from crczp.sandbox_instance_app.lib.stage_cancellation import (
    LIVENESS_POLL_SECONDS,
    STOP_CONFIRMATION_SECONDS,
)
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    AllocationStage,
    Pool,
    SandboxAllocationUnit,
)

LOG = structlog.get_logger()

CREATION_JOB_NAME = '_create_allocation_jobs'


class CancellationAction(StrEnum):
    """What the cancellation did to one unit."""

    REMOVED = 'removed'
    STOPPED = 'stopped'
    SKIPPED = 'skipped'
    FAILED = 'failed'


@dataclass(frozen=True)
class UnitCancellation:
    """
    The result of cancelling one unit.

    A stopped unit has its cleanup queued unless its stop is still pending. The reason is set
    for a skipped or failed unit.
    """

    unit_id: int
    stack_name: str
    action: CancellationAction
    stop_pending: bool = False
    reason: str = ''


def cancel_pool_allocations(
    pool: Pool, wait_seconds: float = STOP_CONFIRMATION_SECONDS
) -> list[UnitCancellation]:
    """
    Cancel the allocations of the pool.

    Queued units are removed. Running builds are stopped together, waiting once for all of
    them, and then cleaned up. Ready sandboxes, failed allocations and units being cleaned up
    are left alone and are not in the result.

    :param pool: The pool whose allocations are cancelled.
    :param wait_seconds: How long to wait for the stops to be confirmed, in seconds.
    :returns: One result per cancelled or failed unit.
    """
    return cancel_units(_get_queued_units(pool), _get_building_units(pool), wait_seconds)


def cancel_queued_request(allocation_request: AllocationRequest) -> bool:
    """
    Cancel an allocation request whose stages do not exist yet, the way a pool run does.

    :returns: Whether the stop of a build the unit started meanwhile is still pending.
    :raises ValidationError: The allocation could not be cancelled.
    """
    unit = SandboxAllocationUnit.objects.select_related('pool').get(
        pk=allocation_request.allocation_unit.id
    )
    results = cancel_units([unit], [], STOP_CONFIRMATION_SECONDS)
    for result in results:
        if result.action == CancellationAction.FAILED:
            raise exceptions.ValidationError(result.reason)
    return any(result.stop_pending for result in results)


def cancel_units(
    queued_units: list[SandboxAllocationUnit],
    building_units: list[SandboxAllocationUnit],
    wait_seconds: float,
) -> list[UnitCancellation]:
    """
    Cancel units whose allocation job is queued and units whose stages exist.

    The creation jobs of queued units are cancelled in Redis and read back. A queued unit is
    removed only while it still has no stages, checked under its row lock, which also stops
    a job that starts meanwhile from building it. A unit that got stages meanwhile is stopped
    like a running build, once the creation job has queued every stage job. When a queued
    creation job cannot be read, no queued unit is touched and each is a failed result.

    :param wait_seconds: How long the whole call waits for stage jobs and stop confirmations.

    :returns: One result per unit that was cancelled or failed.
    """
    deadline = time.monotonic() + wait_seconds
    results: list[UnitCancellation] = []
    queued_by_id = {unit.id: unit for unit in queued_units}
    if _cancel_creation_jobs(set(queued_by_id)):
        results.extend(
            _make_result(
                unit,
                CancellationAction.FAILED,
                reason='A queued allocation job could not be read, so it is unknown whether it'
                ' builds this unit.',
            )
            for unit in queued_units
        )
        queued_by_id = {}
    removed_ids = _remove_stageless_units(queued_by_id)
    results.extend(
        _make_result(queued_by_id[unit_id], CancellationAction.REMOVED) for unit_id in removed_ids
    )

    building_ids = {unit.id for unit in building_units}
    started_ids = _get_existing_ids(set(queued_by_id) - removed_ids - building_ids)
    ready_ids = _await_stage_jobs(started_ids, _seconds_left(deadline))
    results.extend(
        _make_result(
            queued_by_id[unit_id],
            CancellationAction.FAILED,
            reason='The allocation job neither stayed queued nor created its stages in time.',
        )
        for unit_id in started_ids - ready_ids
    )

    started_units = [queued_by_id[unit_id] for unit_id in ready_ids]
    results.extend(_stop_and_clean_up([*building_units, *started_units], _seconds_left(deadline)))
    return results


def _seconds_left(deadline: float) -> float:
    return max(deadline - time.monotonic(), 0)


def _allocation_stages() -> QuerySet[AllocationStage]:
    return AllocationStage.objects.filter(
        allocation_request_fk_many__allocation_unit=OuterRef('pk')
    )


def _get_queued_units(pool: Pool) -> list[SandboxAllocationUnit]:
    """Units of the pool without allocation stages, whose creation job has not built them."""
    return list(
        SandboxAllocationUnit.objects
        .filter(pool=pool, cleanup_request__isnull=True)
        .exclude(Exists(_allocation_stages()))
        .select_related('pool')
    )


def _get_building_units(pool: Pool) -> list[SandboxAllocationUnit]:
    """
    Units of the pool not being cleaned up, with an unfinished allocation stage or with a
    failed one and a NetBird provisioning job still tracked.
    """
    pending_netbird_job = AllocationRequest.objects.filter(
        allocation_unit=OuterRef('pk'), netbird_job_id__isnull=False
    )
    return list(
        SandboxAllocationUnit.objects
        .filter(pool=pool, cleanup_request__isnull=True)
        .filter(
            Exists(_allocation_stages().filter(finished=False))
            | (Exists(_allocation_stages().filter(failed=True)) & Exists(pending_netbird_job))
        )
        .select_related('pool')
    )


def _make_result(
    unit: SandboxAllocationUnit,
    action: CancellationAction,
    stop_pending: bool = False,
    reason: str = '',
) -> UnitCancellation:
    return UnitCancellation(
        unit_id=unit.id,
        stack_name=unit.get_stack_name(),
        action=action,
        stop_pending=stop_pending,
        reason=reason,
    )


def _cancel_creation_jobs(unit_ids: set[int]) -> bool:
    """
    Cancel the queued creation jobs that build only the given units, and read their status back.

    A job that also builds other units is left queued; it skips the units removed meanwhile.

    :returns: Whether a queued job was left alone because its function or arguments cannot be
        loaded, so that it is unknown whether it builds one of the units.
    """
    queue = request_handlers.RequestHandler.queue_default
    unreadable = False
    for job in queue.get_jobs():
        try:
            if job.func_name != CREATION_JOB_NAME:
                continue
            job_unit_ids = {unit.id for unit in job.args[0]}
        except Exception:  # pylint: disable=broad-exception-caught
            LOG.exception('queued_job_unreadable', job_id=job.id)
            unreadable = True
            continue
        if not job_unit_ids or not job_unit_ids <= unit_ids:
            continue
        # LREM is atomic with a worker's pop: exactly one of them gets the job.
        if not queue.remove(job):
            LOG.warning('allocation_job_started_meanwhile', job_id=job.id, unit_ids=job_unit_ids)
            continue
        job.cancel()
        if job.get_status(refresh=True) == JobStatus.CANCELED:
            LOG.info('allocation_job_cancelled', job_id=job.id, unit_ids=job_unit_ids)
        else:
            LOG.warning('allocation_job_cancel_not_confirmed', job_id=job.id)
    return unreadable


def _remove_stageless_units(units_by_id: dict[int, SandboxAllocationUnit]) -> set[int]:
    """
    Delete the units that still have no stages and no cleanup, and lower the pool sizes.

    The check and the delete run under the units' row locks, which the creation job takes as
    well: a job that committed stages first makes the unit stay, a delete that committed
    first makes the job skip the unit.

    :returns: Ids of the deleted units.
    """
    removed_ids: set[int] = set()
    unit_ids_by_pool: dict[int, list[int]] = {}
    for unit in units_by_id.values():
        unit_ids_by_pool.setdefault(unit.pool.id, []).append(unit.id)
    for pool_id, unit_ids in unit_ids_by_pool.items():
        with transaction.atomic():
            pool = Pool.objects.select_for_update().get(pk=pool_id)
            # Locked in a statement of its own: a row that only waited for the lock is not
            # checked against the condition again, so the condition is read after the lock.
            locked_ids = list(
                SandboxAllocationUnit.objects
                .select_for_update(of=('self',))
                .filter(pk__in=unit_ids)
                .values_list('pk', flat=True)
            )
            still_queued = list(
                SandboxAllocationUnit.objects
                .filter(pk__in=locked_ids, cleanup_request__isnull=True)
                .exclude(Exists(_allocation_stages()))
                .values_list('pk', flat=True)
            )
            for request in AllocationRequest.objects.filter(
                allocation_unit_id__in=still_queued, request_group__isnull=False
            ).select_related('request_group'):
                request.request_group.on_allocation_removed()
            SandboxAllocationUnit.objects.filter(pk__in=still_queued).delete()
            pool.size = max(pool.size - len(still_queued), 0)
            pool.save()
        removed_ids.update(still_queued)
    return removed_ids


def _get_existing_ids(unit_ids: set[int]) -> set[int]:
    return set(SandboxAllocationUnit.objects.filter(pk__in=unit_ids).values_list('pk', flat=True))


def _await_stage_jobs(unit_ids: set[int], wait_seconds: float) -> set[int]:
    """
    Wait until the creation job has queued every stage job of the units.

    :returns: Ids of the units that got there in time.
    """
    deadline = time.monotonic() + wait_seconds
    waiting_ids = set(unit_ids)
    ready_ids: set[int] = set()
    while waiting_ids:
        newly_ready = set(
            SandboxAllocationUnit.objects
            .filter(pk__in=waiting_ids)
            .filter(Exists(_allocation_stages()))
            .exclude(Exists(_allocation_stages().filter(rq_job__isnull=True)))
            .values_list('pk', flat=True)
        )
        ready_ids |= newly_ready
        waiting_ids -= newly_ready
        if time.monotonic() >= deadline:
            break
        time.sleep(LIVENESS_POLL_SECONDS)
    return ready_ids


def _stop_and_clean_up(
    units: Iterable[SandboxAllocationUnit], wait_seconds: float
) -> list[UnitCancellation]:
    """Stop the builds of the units together, then queue the cleanup of each confirmed stop."""
    unfinished_requests = [
        allocation_request
        for allocation_request in AllocationRequest.objects.filter(
            allocation_unit__in=list(units)
        ).select_related('allocation_unit__pool')
        if not allocation_request.is_settled
    ]
    stop_pending_per_request = request_handlers.cancel_requests(unfinished_requests, wait_seconds)
    results = []
    for allocation_request, stop_pending in zip(
        unfinished_requests, stop_pending_per_request, strict=True
    ):
        unit = allocation_request.allocation_unit
        if stop_pending is None:
            results.append(
                _make_result(
                    unit,
                    CancellationAction.SKIPPED,
                    reason='The allocation finished before it could be cancelled.',
                )
            )
        elif stop_pending:
            results.append(_make_result(unit, CancellationAction.STOPPED, stop_pending=True))
        else:
            results.append(_clean_up_stopped_unit(unit))
    return results


def _clean_up_stopped_unit(unit: SandboxAllocationUnit) -> UnitCancellation:
    """Queue the cleanup of a unit whose build stopped; a failure stays with this unit."""
    try:
        requests.create_cleanup_request_after_stop(unit)
    except exceptions.ValidationError as exc:
        return _make_result(
            unit, CancellationAction.FAILED, reason=f'Stopped, but not cleaned up: {exc}'
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOG.exception('cancelled_unit_cleanup_failed', unit_id=unit.id, pool_id=unit.pool.id)
        return _make_result(
            unit, CancellationAction.FAILED, reason=f'Stopped, but not cleaned up: {exc}'
        )
    return _make_result(unit, CancellationAction.STOPPED)
