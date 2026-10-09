"""Tests for the allocation unit state expressions in lib/unit_filters."""

from typing import Any

import pytest

from crczp.sandbox_instance_app.lib import unit_filters
from crczp.sandbox_instance_app.models import SandboxAllocationUnit
from crczp.sandbox_instance_app.serializers import SandboxAllocationUnitSerializer
from crczp.sandbox_instance_app.tests.conftest import set_stage_failed

pytestmark = pytest.mark.django_db


def matches(unit: SandboxAllocationUnit, expression: Any) -> bool:
    """Whether the unit is selected by the expression."""
    return SandboxAllocationUnit.objects.filter(expression, pk=unit.pk).exists()


def test_new_unit_is_active(allocation_unit):
    """A freshly queued unit, before any stage exists, is active."""
    assert matches(allocation_unit, unit_filters.active())
    assert not matches(allocation_unit, unit_filters.allocation_failed())
    assert not matches(allocation_unit, unit_filters.has_cleanup())


def test_allocated_unit_is_active(sandbox_finished):
    """A unit whose allocation finished is active."""
    assert matches(sandbox_finished.allocation_unit, unit_filters.active())


def test_failed_allocation_is_not_active(sandbox_failed_user_stage):
    """A unit with a failed allocation stage is not active."""
    unit = sandbox_failed_user_stage.allocation_unit

    assert matches(unit, unit_filters.allocation_failed())
    assert not matches(unit, unit_filters.active())


def test_running_cleanup_is_active(cleanup_request_started):
    """A unit still being cleaned up is active and has an unfinished cleanup."""
    unit = cleanup_request_started.allocation_unit

    assert matches(unit, unit_filters.has_cleanup())
    assert matches(unit, unit_filters.cleanup_unfinished())
    assert matches(unit, unit_filters.active())


def test_finished_cleanup_is_not_active(cleanup_request_finished):
    """A unit whose cleanup finished is not active."""
    unit = cleanup_request_finished.allocation_unit

    assert not matches(unit, unit_filters.cleanup_unfinished())
    assert not matches(unit, unit_filters.active())


def test_failed_cleanup_is_finished_and_not_active(cleanup_request_started):
    """A failed cleanup counts as finished, as CleanupRequest.is_finished does."""
    set_stage_failed(cleanup_request_started.stackcleanupstage)
    unit = cleanup_request_started.allocation_unit

    assert cleanup_request_started.is_finished
    assert not matches(unit, unit_filters.cleanup_unfinished())
    assert not matches(unit, unit_filters.active())


class TestSandboxAllocationUnitSerializer:
    """The creator and sandbox fields of the allocation unit representation."""

    def test_trainee_unit_exposes_creator_sub_and_sandbox(self, sandbox_finished):
        """A trainee's unit shows who allocated it, when, and its sandbox id."""
        unit = sandbox_finished.allocation_unit
        unit.created_by_sub = 'trainee-sub'
        unit.save()

        data = SandboxAllocationUnitSerializer(unit).data

        assert data['created_by_sub'] == 'trainee-sub'
        assert data['created_at'] is not None
        assert data['sandbox_id'] == sandbox_finished.id

    def test_organizer_unit_without_sandbox(self, allocation_unit):
        """An organizer's unit has no creator sub, and no sandbox id until it is built."""
        data = SandboxAllocationUnitSerializer(allocation_unit).data

        assert data['created_by_sub'] is None
        assert data['sandbox_id'] is None

    def test_creator_fields_are_read_only(self, allocation_unit):
        """A PATCH cannot rewrite who created the unit."""
        serializer = SandboxAllocationUnitSerializer(
            allocation_unit,
            data={'created_by_sub': 'someone-else', 'comment': 'note'},
            partial=True,
        )
        assert serializer.is_valid()
        serializer.save()

        allocation_unit.refresh_from_db()
        assert allocation_unit.created_by_sub is None
        assert allocation_unit.comment == 'note'
