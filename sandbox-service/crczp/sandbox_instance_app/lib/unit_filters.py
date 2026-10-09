"""Database expressions for the state of sandbox allocation units.

Each function returns an expression for SandboxAllocationUnit.objects.filter() or
.exclude(), so a state check costs one query for any number of units instead of a few
queries per unit in Python. This module imports only models, so pools, requests and the
request handlers can all use it without an import cycle.
"""

from typing import Any

from django.db.models import Exists, OuterRef, Q, QuerySet

from crczp.sandbox_instance_app.models import AllocationStage, CleanupRequest, CleanupStage


def _allocation_stages() -> QuerySet[AllocationStage, Any]:
    return AllocationStage.objects.filter(
        allocation_request_fk_many__allocation_unit=OuterRef('pk')
    )


def _cleanup_stages() -> QuerySet[CleanupStage, Any]:
    return CleanupStage.objects.filter(cleanup_request_fk_many__allocation_unit=OuterRef('pk'))


def allocation_failed() -> Exists:
    """An allocation stage of the unit failed or was cancelled."""
    return Exists(_allocation_stages().filter(failed=True))


def has_cleanup() -> Exists:
    """The unit has a cleanup request, in whatever state."""
    return Exists(CleanupRequest.objects.filter(allocation_unit=OuterRef('pk')))


def cleanup_unfinished() -> Exists:
    """The unit has a cleanup request with a stage that has not finished yet.

    Matches CleanupRequest.is_finished being False. A failed cleanup is finished: the
    failing stage is marked finished and the request handler cancels the rest.
    """
    return Exists(_cleanup_stages().filter(finished=False))


def active() -> Q:
    """The unit holds, or is still building, a sandbox.

    Its allocation has not failed and it is either not being cleaned up or its cleanup is
    still running. A unit whose cleanup failed is not active.
    """
    return ~allocation_failed() & (~has_cleanup() | cleanup_unfinished())
