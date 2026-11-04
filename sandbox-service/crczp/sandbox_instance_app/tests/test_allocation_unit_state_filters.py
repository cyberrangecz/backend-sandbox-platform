"""Tests for the state expressions of SandboxAllocationUnit."""

import pytest

from crczp.sandbox_instance_app.models import CleanupRequest, SandboxAllocationUnit
from crczp.sandbox_instance_app.tests.conftest import set_stage_failed

pytestmark = pytest.mark.django_db


def is_active(unit: SandboxAllocationUnit) -> bool:
    """Whether the active expression selects the unit."""
    return SandboxAllocationUnit.objects.filter(
        SandboxAllocationUnit.is_active(), pk=unit.pk
    ).exists()


def is_cleaning_up(unit: SandboxAllocationUnit) -> bool:
    """Whether the unit's cleanup is queued or running."""
    return SandboxAllocationUnit.objects.filter(
        SandboxAllocationUnit.has_unfinished_cleanup(), pk=unit.pk
    ).exists()


def test_new_unit_is_active(allocation_unit):
    """A freshly queued unit, before any stage exists, is active."""
    assert is_active(allocation_unit)
    assert not is_cleaning_up(allocation_unit)


def test_allocated_unit_is_active(sandbox_finished):
    """A unit whose allocation finished is active."""
    assert is_active(sandbox_finished.allocation_unit)


def test_failed_allocation_is_not_active(sandbox_failed_user_stage):
    """A unit with a failed allocation stage is not active."""
    assert not is_active(sandbox_failed_user_stage.allocation_unit)


def test_queued_cleanup_is_active_and_cleaning_up(sandbox_finished):
    """A unit whose cleanup request has no stages yet is active and being cleaned up."""
    unit = sandbox_finished.allocation_unit
    CleanupRequest.objects.create(allocation_unit=unit)

    assert is_cleaning_up(unit)
    assert is_active(unit)


def test_running_cleanup_is_active_and_cleaning_up(cleanup_request_started):
    """A unit still being cleaned up is active and has an unfinished cleanup."""
    unit = cleanup_request_started.allocation_unit

    assert is_cleaning_up(unit)
    assert is_active(unit)


def test_finished_cleanup_is_not_active(cleanup_request_finished):
    """A unit whose cleanup finished is neither active nor being cleaned up."""
    unit = cleanup_request_finished.allocation_unit

    assert not is_cleaning_up(unit)
    assert not is_active(unit)


def test_failed_cleanup_is_finished_and_not_active(cleanup_request_started):
    """A failed cleanup counts as finished, as CleanupRequest.is_finished does."""
    set_stage_failed(cleanup_request_started.stackcleanupstage)
    unit = cleanup_request_started.allocation_unit

    assert cleanup_request_started.is_finished
    assert not is_cleaning_up(unit)
    assert not is_active(unit)
