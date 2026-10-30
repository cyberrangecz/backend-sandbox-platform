"""Tests for Ansible stage API views."""

import pytest
from rest_framework.reverse import reverse
from rest_framework.test import APIClient, APIRequestFactory

from crczp.sandbox_ansible_app.views import NetworkingAnsibleAllocationStageDetailView
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    CleanupRequest,
    Pool,
    SandboxAllocationUnit,
)

pytestmark = pytest.mark.django_db

ALLOCATION_REQUEST_ID = 1


class TestStages:
    """Tests for Ansible stage views."""

    @pytest.fixture
    def arf(self):
        """Return an APIRequestFactory instance."""
        return APIRequestFactory()

    def test_networking_stage_views(self, arf):
        """Verify the networking Ansible allocation stage view returns expected data."""
        request_kwargs = {'request_id': ALLOCATION_REQUEST_ID}
        request = arf.get(reverse('networking-ansible-allocation-stage', kwargs=request_kwargs))
        response = NetworkingAnsibleAllocationStageDetailView.as_view()(
            request, request_id=ALLOCATION_REQUEST_ID
        )

        assert response.data['id'] == 2
        assert response.data['rev'] == '04e97bb05456b37a74cd28732547b65f213e1b99'
        assert response.data['request_id'] == 1


@pytest.mark.parametrize(
    'route_name, request_model',
    [
        ('networking-ansible-allocation-stage', AllocationRequest),
        ('user-ansible-allocation-stage', AllocationRequest),
        ('networking-ansible-cleanup-stage', CleanupRequest),
        ('user-ansible-cleanup-stage', CleanupRequest),
    ],
)
def test_stage_detail_of_a_request_without_that_stage_answers_not_found(route_name, request_model):
    """A request whose ansible stage does not exist yet is answered with 404, not 500."""
    request = request_model.objects.create(
        allocation_unit=SandboxAllocationUnit.objects.create(pool=Pool.objects.first())
    )

    response = APIClient().get(reverse(route_name, kwargs={'request_id': request.pk}))

    assert response.status_code == 404
