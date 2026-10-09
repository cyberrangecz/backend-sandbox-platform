"""Tests for the sandbox allocation unit API views."""

import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory

from crczp.sandbox_instance_app.models import SandboxAllocationUnit
from crczp.sandbox_instance_app.views import SandboxAllocationUnitListCreateView

pytestmark = pytest.mark.django_db


class TestSandboxAllocationUnitList:
    """GET pools/{pool_id}/sandbox-allocation-units."""

    def test_sort_by_allocation_unit_id_sorts_by_id(self, pool, created_by):
        """The frontend's 'allocation_unit_id' sort name is accepted as the unit id."""
        units = [
            SandboxAllocationUnit.objects.create(pool=pool, created_by=created_by) for _ in range(3)
        ]
        request = APIRequestFactory().get(
            '/', {'sort_by': 'allocation_unit_id', 'order': 'desc', 'page': 1, 'page_size': 10}
        )

        response = SandboxAllocationUnitListCreateView.as_view()(request, pool_id=pool.id)

        assert response.status_code == status.HTTP_200_OK
        # Fixture data loaded by other apps' tests may share the pool id, so check the order
        # and that the units are there rather than an exact list.
        ids = [unit['id'] for unit in response.data['results']]
        assert ids == sorted(ids, reverse=True)
        assert {unit.id for unit in units} <= set(ids)

    def test_unknown_sort_field_is_bad_request(self, pool):
        """An unknown sort field answers 400 instead of failing with a 500."""
        request = APIRequestFactory().get('/', {'sort_by': 'no_such_field'})

        response = SandboxAllocationUnitListCreateView.as_view()(request, pool_id=pool.id)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
