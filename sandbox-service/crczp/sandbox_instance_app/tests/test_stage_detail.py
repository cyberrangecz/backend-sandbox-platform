"""Tests for the stage detail endpoints of allocation and cleanup requests."""

from types import SimpleNamespace

import pytest
from rest_framework.test import APIRequestFactory

from crczp.sandbox_instance_app import views
from crczp.sandbox_instance_app.views import (
    TerraformAllocationStageDetailView,
    TerraformCleanupStageDetailView,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fixture_stage_handler_without_client(mocker):
    """Wrap the allocation stage in a handler that builds no cloud client."""
    mocker.patch.object(
        views.stage_handlers,
        'AllocationStackStageHandler',
        side_effect=lambda stage: SimpleNamespace(stage=stage),
    )


class TestStageDetail:
    """Allocation and cleanup stage detail views."""

    def test_allocation_stage_missing_answers_not_found(self, allocation_request):
        """A request without its stage answers 404 naming the missing stage."""
        response = TerraformAllocationStageDetailView.as_view()(
            APIRequestFactory().get('/'), request_id=allocation_request.id
        )

        assert response.status_code == 404
        assert 'not created yet' in response.data['detail']

    def test_cleanup_stage_missing_answers_not_found(self, cleanup_request):
        """A request without its stage answers 404 naming the missing stage."""
        response = TerraformCleanupStageDetailView.as_view()(
            APIRequestFactory().get('/'), request_id=cleanup_request.id
        )

        assert response.status_code == 404
        assert 'not created yet' in response.data['detail']

    def test_allocation_stage_answers_its_data(self, allocation_stage_stack):
        """A request with its stage answers 200 with the stage."""
        response = TerraformAllocationStageDetailView.as_view()(
            APIRequestFactory().get('/'), request_id=allocation_stage_stack.allocation_request.id
        )

        assert response.status_code == 200
        assert response.data['id'] == allocation_stage_stack.id

    def test_cleanup_stage_answers_its_data(self, cleanup_stage_stack):
        """A request with its stage answers 200 with the stage."""
        response = TerraformCleanupStageDetailView.as_view()(
            APIRequestFactory().get('/'), request_id=cleanup_stage_stack.cleanup_request.id
        )

        assert response.status_code == 200
        assert response.data['id'] == cleanup_stage_stack.id
