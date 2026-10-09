"""Tests for the sandbox allocation unit API views."""

import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory

from crczp.sandbox_instance_app.views import SandboxAllocationUnitListCreateView

pytestmark = pytest.mark.django_db


class TestSandboxAllocationUnitList:
    """GET pools/{pool_id}/sandbox-allocation-units."""

    def test_unknown_sort_field_is_bad_request(self, pool):
        """An unknown sort field answers 400 instead of failing with a 500."""
        request = APIRequestFactory().get('/', {'sort_by': 'no_such_field'})

        response = SandboxAllocationUnitListCreateView.as_view()(request, pool_id=pool.id)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
