"""Tests for the creator and sandbox fields of SandboxAllocationUnitSerializer."""

import pytest

from crczp.sandbox_instance_app.serializers import SandboxAllocationUnitSerializer

pytestmark = pytest.mark.django_db


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
