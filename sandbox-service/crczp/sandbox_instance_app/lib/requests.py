"""Business logic for creating and managing sandbox allocation and cleanup requests."""

import enum
from collections.abc import Iterable
from functools import partial
from typing import Any

import structlog
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_instance_app.lib import netbird, request_handlers, sandboxes, unit_filters
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

    created_by_sub is the OIDC sub of a trainee allocating a sandbox for themselves.
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

        transaction.on_commit(
            partial(request_handlers.AllocationRequestHandler().enqueue_request, units, created_by)
        )

    return units


def cancel_allocation_request(alloc_req: AllocationRequest) -> None:
    """(Soft) cancel all stages of the Allocation Request."""
    request_handlers.AllocationRequestHandler().cancel_request(alloc_req)


class CleanupNotAllowedError(exceptions.ValidationError):
    """Raised when a unit cannot be cleaned up yet, not even by force."""


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


def create_cleanup_request_force(allocation_unit: SandboxAllocationUnit, delete_pool: bool) -> None:
    """Create cleanup request and enqueue it. Immediately delete sandbox from database.
    The force parameter forces the deletion.

    :raises CleanupNotAllowedError: The unit cannot be cleaned up yet.
    """
    if (
        hasattr(allocation_unit, 'cleanup_request')
        and not allocation_unit.cleanup_request.is_finished
    ):
        return

    _ensure_cleanup_allowed(allocation_unit)

    try:
        sandbox = allocation_unit.sandbox
    except ObjectDoesNotExist:
        sandbox = None
    else:
        if sandbox is not None and hasattr(sandbox, 'lock'):
            sandbox.lock.delete()

    if not allocation_unit.allocation_request.is_finished:
        cancel_allocation_request(allocation_unit.allocation_request)

    if sandbox:
        sandboxes.clear_cache(sandbox)
        netbird.destroy_netbird_for_sandbox(sandbox)
        sandbox.delete()

    request_handlers.CleanupRequestHandler(delete_pool=delete_pool).enqueue_request(allocation_unit)


def create_cleanup_request(allocation_unit: SandboxAllocationUnit) -> None:
    """Create cleanup request and enqueue it. Immediately delete sandbox from database."""
    try:
        sandbox = allocation_unit.sandbox
    except ObjectDoesNotExist:
        sandbox = None
    else:
        assert sandbox is not None
        if hasattr(sandbox, 'lock'):
            raise exceptions.ValidationError(f'Sandbox ID={sandbox.id} is locked. Unlock it first.')

    _ensure_cleanup_allowed(allocation_unit)

    if not allocation_unit.allocation_request.is_finished:
        raise exceptions.ValidationError(
            f'Create sandbox allocation request ID={allocation_unit.allocation_request.id}'
            f' has not finished yet. You need to cancel it first.'
        )

    if hasattr(allocation_unit, 'cleanup_request'):
        raise exceptions.ValidationError(
            f'Allocation unit ID={allocation_unit.id} already has a cleanup request '
            f'ID={allocation_unit.cleanup_request.id}. Delete it first.'
        )

    if sandbox:
        sandboxes.clear_cache(sandbox)
        netbird.destroy_netbird_for_sandbox(sandbox)
        sandbox.delete()

    request_handlers.CleanupRequestHandler().enqueue_request(allocation_unit)


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


def cancel_cleanup_request(cleanup_req: CleanupRequest) -> None:
    """(Soft) cancel all stages of the Cleanup Request."""
    request_handlers.CleanupRequestHandler().cancel_request(cleanup_req)


def delete_cleanup_request(request: CleanupRequest) -> None:
    """Delete given cleanup request."""
    if not request.is_finished:
        raise exceptions.ValidationError(
            'The cleanup request is not finished. You need to cancel it first.'
        )
    request.delete()


class StuckUnits(enum.Enum):
    """Kinds of allocation units the pool maintenance actions remove."""

    # Allocation not started yet; nothing exists in the cloud.
    QUEUED = 'queued'
    # First (stack) stage running, e.g. hung; its cloud resources stay behind.
    FIRST_STAGE_RUNNING = 'first_stage_running'
    # Cleanup not finished, e.g. hung; resources it did not remove stay behind.
    CLEANUP_UNFINISHED = 'cleanup_unfinished'

    def unit_filter(self) -> Any:
        """The database filter selecting units of this kind."""
        match self:
            case StuckUnits.QUEUED:
                return unit_filters.allocation_queued()
            case StuckUnits.FIRST_STAGE_RUNNING:
                return unit_filters.first_stage_running()
            case StuckUnits.CLEANUP_UNFINISHED:
                return unit_filters.cleanup_unfinished()


def force_remove_units(pool: Pool, kind: StuckUnits) -> int:
    """Cancel the requests of the pool's units of the given kind and remove the units.

    Only the database records go, and the sandboxes' NetBird resources, whose ids live only
    there. Cloud resources the units already have are not touched and must be removed by
    hand. Each unit is checked again under its row lock, so one that has moved on since is
    left alone.

    :return: The number of removed units.
    """
    unit_ids = SandboxAllocationUnit.objects.filter(kind.unit_filter(), pool=pool).values_list(
        'id', flat=True
    )
    return sum(_force_remove_unit(pool.id, unit_id, kind) for unit_id in list(unit_ids))


def _force_remove_unit(pool_id: int, unit_id: int, kind: StuckUnits) -> bool:
    """Cancel the unit's request and remove the unit; return whether it was removed."""
    with transaction.atomic():
        unit = (
            SandboxAllocationUnit.objects
            .select_for_update()
            .filter(kind.unit_filter(), pk=unit_id)
            .first()
        )
        if unit is None:
            return False
        try:
            if kind is StuckUnits.CLEANUP_UNFINISHED:
                cancel_cleanup_request(unit.cleanup_request)
            elif hasattr(unit, 'allocation_request'):
                cancel_allocation_request(unit.allocation_request)
        except (exceptions.ValidationError, ObjectDoesNotExist):
            pass  # Already finished, or its stages do not exist (yet).
        try:
            sandbox = unit.sandbox
        except ObjectDoesNotExist:
            sandbox = None

    if sandbox is not None:
        # Talks to NetBird, so it runs outside any transaction.
        netbird.destroy_netbird_for_sandbox(sandbox)

    with transaction.atomic():
        pool = Pool.objects.select_for_update().get(pk=pool_id)
        if sandbox is not None:
            sandboxes.clear_cache(sandbox)
            SandboxLock.objects.filter(sandbox=sandbox).delete()
            Sandbox.objects.filter(pk=sandbox.pk).delete()
        if not SandboxAllocationUnit.objects.filter(pk=unit_id).exists():
            return False
        SandboxAllocationUnit.objects.filter(pk=unit_id).delete()
        pool.size = max(pool.size - 1, 0)
        pool.save()
    LOG.warning(
        'allocation_unit_force_removed',
        unit_id=unit_id,
        pool_id=pool_id,
        kind=kind.value,
        sandbox_id=sandbox.pk if sandbox is not None else None,
    )
    return True


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
