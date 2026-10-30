"""Business logic for creating and managing sandbox allocation and cleanup requests."""

import enum
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Any

import structlog
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import F
from django.http import Http404

from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_instance_app.lib import (
    allocation_cancellation,
    request_handlers,
    sandboxes,
)
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    CleanupRequest,
    Pool,
    Sandbox,
    SandboxAllocationUnit,
    SandboxLock,
)

LOG = structlog.get_logger()


class StageState(enum.Enum):
    """Enumeration of possible states for a sandbox request stage."""

    IN_QUEUE = 'IN_QUEUE'
    RUNNING = 'RUNNING'
    FINISHED = 'FINISHED'
    FAILED = 'FAILED'


def restart_allocation_stages(unit: SandboxAllocationUnit) -> SandboxAllocationUnit:
    """Restarts failed allocation stages and recreates the existing sandbox and request in the
    database.
    """

    request_handlers.AllocationRequestHandler().restart_request(unit)
    return unit


def create_allocations_requests(
    pool: Pool, count: int, created_by: User | None, *, created_by_sub: str | None = None
) -> list[SandboxAllocationUnit]:
    """Batch version of create_allocation_request. Create count Sandbox Requests.

    created_by_sub is the OIDC sub of a trainee allocating a sandbox for themselves. The pool
    size grows in the same transaction as the unit rows.
    """

    with transaction.atomic():
        units = []
        for _ in range(count):
            unit = SandboxAllocationUnit.objects.create(
                pool=pool, created_by=created_by, created_by_sub=created_by_sub
            )
            # Create the AllocationRequest row synchronously, in the request thread,
            # so the allocation-units listing never returns a freshly created unit
            # with allocation_request=null (the frontend renders that null as
            # "Unknown error / Unknown stage In Queue"). The expensive work — SSH
            # keygen, Sandbox creation and stage enqueuing — still happens later on
            # the default worker via enqueue_request. Until the worker creates the
            # stage rows, get_allocation_request_stages_state reports all stages as
            # IN_QUEUE, which is the correct state for a just-queued request.
            AllocationRequest.objects.create(allocation_unit=unit)
            units.append(unit)
        Pool.objects.filter(pk=pool.pk).update(size=F('size') + count)

        if units:
            transaction.on_commit(
                partial(
                    request_handlers.AllocationRequestHandler().enqueue_request, units, created_by
                )
            )

    return units


def cancel_allocation_request(alloc_req: AllocationRequest) -> bool:
    """Cancel all stages of the Allocation Request; return whether a stop is still pending."""
    return request_handlers.AllocationRequestHandler().cancel_request(alloc_req)


def cancel_allocation_request_and_clean_up(alloc_req: AllocationRequest) -> bool:
    """Cancel the Allocation Request and queue the cleanup of its unit once its jobs ended.

    While the stop of a stage is pending, no cleanup is queued. A request whose stages do not
    exist yet is queued: its job and unit are removed the way Cancel all allocations removes them.

    :returns: Whether a stop is still pending.
    :raises ValidationError: The unit cannot be cleaned up, or the queued allocation could not
        be cancelled.
    """
    _ensure_cleanup_not_blocked(alloc_req.allocation_unit, _get_sandbox(alloc_req.allocation_unit))
    if not alloc_req.stages.exists():
        return allocation_cancellation.cancel_queued_request(alloc_req)
    stop_pending = False
    if not _is_failed_and_settled(alloc_req):
        stop_pending = cancel_allocation_request(alloc_req)
    if not stop_pending:
        create_cleanup_request_after_stop(alloc_req.allocation_unit)
    return stop_pending


def _is_failed_and_settled(alloc_req: AllocationRequest) -> bool:
    """Whether the allocation ended with a failed stage, so only its cleanup is missing."""
    return alloc_req.is_settled and alloc_req.stages.filter(failed=True).exists()


def create_cleanup_request_after_stop(allocation_unit: SandboxAllocationUnit) -> None:
    """
    Create the cleanup request of a unit whose build has stopped.

    A cleanup request queued meanwhile by another process counts as success.

    :raises ValidationError: The unit cannot be cleaned up.
    """
    try:
        create_cleanup_request(allocation_unit)
    except exceptions.ValidationError:
        if not CleanupRequest.objects.filter(allocation_unit=allocation_unit).exists():
            raise


class CleanupNotAllowedError(exceptions.ValidationError):
    """Raised when a unit cannot be cleaned up yet, not even by force."""


def _ensure_cleanup_not_blocked(
    allocation_unit: SandboxAllocationUnit, sandbox: Sandbox | None
) -> None:
    """Raise ValidationError while a lock or an existing cleanup request refuses the cleanup."""
    if sandbox is not None and hasattr(sandbox, 'lock'):
        raise exceptions.ValidationError(f'Sandbox ID={sandbox.id} is locked. Unlock it first.')
    if hasattr(allocation_unit, 'cleanup_request'):
        raise exceptions.ValidationError(
            f'Allocation unit ID={allocation_unit.id} already has a cleanup request '
            f'ID={allocation_unit.cleanup_request.id}. Delete it first.'
        )


def _ensure_cleanup_allowed(allocation_unit: SandboxAllocationUnit) -> None:
    """Raise CleanupNotAllowedError while the unit's sandbox cannot be torn down.

    That is while the first (stack) stage runs, and while the allocation is queued and its
    stages do not exist yet: the queued job would still build the sandbox.
    """
    try:
        stack_stage = allocation_unit.allocation_request.stackallocationstage
    except ObjectDoesNotExist:
        raise CleanupNotAllowedError(
            f'The allocation of unit ID={allocation_unit.id} is queued and has not started yet. '
            'Cancel the queued allocations of the pool, or retry once it starts.'
        ) from None
    if stack_stage.start is not None and not (stack_stage.finished or stack_stage.failed):
        raise CleanupNotAllowedError(
            'Cleanup while the first stage is running is not allowed. '
            'Retry once the first stage is finished or fails.'
        )


def _queue_cleanup(
    allocation_unit: SandboxAllocationUnit, sandbox: Sandbox | None, delete_pool: bool = False
) -> None:
    """
    Create the unit's cleanup request, then delete its sandbox with its lock and cached data,
    in one transaction; the cleanup stages are created by a job queued once it commits.
    """
    with transaction.atomic():
        request_handlers.CleanupRequestHandler(delete_pool=delete_pool).enqueue_request(
            allocation_unit
        )
        if sandbox is not None:
            SandboxLock.objects.filter(sandbox=sandbox).delete()
            sandboxes.clear_cache(sandbox)
            sandbox.delete()


def _has_unfinished_cleanup(allocation_unit: SandboxAllocationUnit) -> bool:
    return SandboxAllocationUnit.objects.filter(
        SandboxAllocationUnit.has_unfinished_cleanup(), pk=allocation_unit.pk
    ).exists()


def create_cleanup_request_force(allocation_unit: SandboxAllocationUnit, delete_pool: bool) -> None:
    """Create cleanup request and enqueue it. Immediately delete sandbox from database.
    The force parameter forces the deletion. A unit whose cleanup is queued or running is
    left alone, except that the cleanup takes over the pool deletion when delete_pool is set.

    :raises CleanupNotAllowedError: The unit cannot be cleaned up yet.
    """
    if _has_unfinished_cleanup(allocation_unit):
        if delete_pool:
            CleanupRequest.objects.filter(allocation_unit=allocation_unit).update(delete_pool=True)
        return

    _ensure_cleanup_allowed(allocation_unit)

    if not allocation_unit.allocation_request.is_settled and cancel_allocation_request(
        allocation_unit.allocation_request
    ):
        raise CleanupNotAllowedError(
            f'The stop of the allocation of unit ID={allocation_unit.id} is not confirmed yet. '
            'Retry once it is.'
        )

    _queue_cleanup(allocation_unit, _get_sandbox(allocation_unit), delete_pool)


def _get_sandbox(allocation_unit: SandboxAllocationUnit) -> Sandbox | None:
    try:
        return allocation_unit.sandbox
    except ObjectDoesNotExist:
        return None


def create_cleanup_request(allocation_unit: SandboxAllocationUnit) -> None:
    """Create cleanup request and enqueue it. Immediately delete sandbox from database."""
    sandbox = _get_sandbox(allocation_unit)
    _ensure_cleanup_not_blocked(allocation_unit, sandbox)
    _ensure_cleanup_allowed(allocation_unit)

    if not allocation_unit.allocation_request.is_settled:
        raise exceptions.ValidationError(
            f'Create sandbox allocation request ID={allocation_unit.allocation_request.id}'
            f' has not finished yet. You need to cancel it first.'
        )

    _queue_cleanup(allocation_unit, sandbox)


def create_cleanup_requests(
    allocation_units: Iterable[SandboxAllocationUnit],
    force: bool = False,
    delete_pool: bool = False,
) -> list[int]:
    """Batch version of create_cleanup_request.

    With force, a unit that cannot be cleaned up yet is skipped instead of stopping the
    batch half done.

    :return: IDs of the skipped units.
    """
    skipped_unit_ids = []
    for unit in allocation_units:
        if not force:
            create_cleanup_request(unit)
            continue
        try:
            create_cleanup_request_force(unit, delete_pool)
        except CleanupNotAllowedError as exc:
            LOG.warning('cleanup_unit_skipped', unit_id=unit.id, reason=str(exc))
            skipped_unit_ids.append(unit.id)
    return skipped_unit_ids


def cancel_cleanup_request(cleanup_req: CleanupRequest) -> bool:
    """Cancel all stages of the Cleanup Request; return whether a stop is still pending."""
    return request_handlers.CleanupRequestHandler().cancel_request(cleanup_req)


def delete_cleanup_request(request: CleanupRequest) -> None:
    """Delete given cleanup request."""
    if not request.is_finished:
        raise exceptions.ValidationError(
            'The cleanup request is not finished. You need to cancel it first.'
        )
    request.delete()


def delete_unit_cleanup_request(allocation_unit: SandboxAllocationUnit) -> None:
    """
    Delete the cleanup request of the unit; refused while the cleanup is queued or running.

    :raises ValidationError: The cleanup is queued or running.
    :raises Http404: The unit has no cleanup request.
    """
    try:
        cleanup_request = allocation_unit.cleanup_request
    except ObjectDoesNotExist as exc:
        raise Http404(f'Allocation unit ID={allocation_unit.id} has no cleanup request.') from exc
    if _has_unfinished_cleanup(allocation_unit):
        raise exceptions.ValidationError(
            f'Cleanup request ID={cleanup_request.id} cannot be deleted while it '
            'is queued or running. Cancel it once it runs.'
        )
    delete_cleanup_request(cleanup_request)


@dataclass
class TraineeCleanupReport:
    """What a run of the trainee sandbox cleanup did, as allocation unit ids."""

    cleaned: list[int] = field(default_factory=list)
    # Their earlier cleanup had failed; it was started again.
    retried: list[int] = field(default_factory=list)
    # Not cleanable yet, e.g. their first stage is running; the next run tries again.
    skipped: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)


def cleanup_expired_trainee_units(
    older_than: datetime, *, dry_run: bool = False
) -> TraineeCleanupReport:
    """Clean up the sandboxes trainees allocated for themselves before older_than.

    Units whose cleanup is queued or running are left alone. A unit whose cleanup failed is cleaned
    up again: a successful cleanup removes its unit, so a unit with a finished cleanup has a
    failed one. With dry_run, only report what would be done.
    """
    report = TraineeCleanupReport()
    units = (
        SandboxAllocationUnit.objects
        .filter(created_by_sub__isnull=False, created_at__lt=older_than)
        .exclude(SandboxAllocationUnit.has_unfinished_cleanup())
        .select_related('pool', 'allocation_request', 'cleanup_request')
        .order_by('id')
    )
    for unit in units:
        done = report.retried if hasattr(unit, 'cleanup_request') else report.cleaned
        if dry_run:
            done.append(unit.id)
            continue
        try:
            create_cleanup_request_force(unit, delete_pool=False)
        except CleanupNotAllowedError:
            report.skipped.append(unit.id)
        # One unit failing, e.g. on an unreachable Redis, must not stop the run.
        except Exception:  # pylint: disable=broad-exception-caught
            LOG.exception('trainee_sandbox_cleanup_failed', unit_id=unit.id, pool_id=unit.pool.id)
            report.failed.append(unit.id)
        else:
            done.append(unit.id)
    return report


def get_allocation_request_stages_state(request: AllocationRequest) -> list[str]:
    """Get AllocationRequests stages state."""
    try:
        stages = [
            request.stackallocationstage,
            request.networkingansibleallocationstage,
            request.useransibleallocationstage,
        ]
    except ObjectDoesNotExist:
        return [StageState.IN_QUEUE.value, StageState.IN_QUEUE.value, StageState.IN_QUEUE.value]

    return _get_request_stages_state(stages)


def get_cleanup_request_stages_state(request: CleanupRequest) -> list[str]:
    """Get CleanupRequests stages state."""
    try:
        stages = [
            request.stackcleanupstage,
            request.networkingansiblecleanupstage,
            request.useransiblecleanupstage,
        ]
    except ObjectDoesNotExist:
        return [StageState.IN_QUEUE.value, StageState.IN_QUEUE.value, StageState.IN_QUEUE.value]

    return _get_request_stages_state(stages)


def _get_request_stages_state(stages: list[Any]) -> list[str]:
    """Get SandboxRequests stages state."""
    stages_state = []

    for stage in stages:
        if stage.end is None and stage.start:
            stages_state.append(StageState.RUNNING.value)
        elif stage.failed:
            stages_state.append(StageState.FAILED.value)
        elif stage.finished:
            stages_state.append(StageState.FINISHED.value)
        else:
            stages_state.append(StageState.IN_QUEUE.value)

    return stages_state
