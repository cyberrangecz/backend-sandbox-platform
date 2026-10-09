"""Tests for the sandbox allocation unit API views."""

import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory

from crczp.sandbox_instance_app.models import SandboxAllocationUnit
from crczp.sandbox_instance_app.tests.conftest import authenticate
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


class TestTraineeAllocation:
    """POST pools/{pool_id}/sandbox-allocation-units with a training access token."""

    @pytest.fixture(autouse=True)
    def no_build(self, mocker):
        """Create the units without checking OpenStack quotas or enqueuing the build."""
        mocker.patch('crczp.sandbox_instance_app.lib.pools.validate_hardware_usage_of_sandboxes')
        mocker.patch(
            'crczp.sandbox_instance_app.lib.requests.request_handlers.AllocationRequestHandler'
        )

    @staticmethod
    def post(pool, user=None, token=None, query=''):
        """POST an allocation, as the user and with the token if given."""
        headers = {'X-Training-Access-Token': token} if token is not None else None
        request = APIRequestFactory().post(
            f'/{query}', {'created_by_sub': 'victim-sub'}, format='json', headers=headers
        )
        if user is not None:
            authenticate(request, user)
        return SandboxAllocationUnitListCreateView.as_view()(request, pool_id=pool.id)

    @pytest.mark.usefixtures('rest_auth_enabled', 'pool_lock')
    def test_trainee_allocates_one_sandbox_for_themselves(
        self, pool, trainee, training_access_token
    ):
        """The unit belongs to the caller; a created_by_sub in the body is ignored."""
        response = self.post(pool, trainee, training_access_token)

        assert response.status_code == status.HTTP_201_CREATED
        assert len(response.data) == 1
        assert response.data[0]['created_by_sub'] == 'trainee-sub'
        unit = SandboxAllocationUnit.objects.get(pk=response.data[0]['id'])
        assert unit.created_by == trainee
        assert unit.created_by_sub == 'trainee-sub'
        pool.refresh_from_db()
        assert pool.size == 1

    @pytest.mark.usefixtures('rest_auth_enabled', 'pool_lock')
    def test_second_sandbox_in_the_pool_is_conflict(self, pool, trainee, training_access_token):
        """A trainee holds one active sandbox per pool."""
        assert self.post(pool, trainee, training_access_token).status_code == 201

        response = self.post(pool, trainee, training_access_token)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert SandboxAllocationUnit.objects.filter(created_by_sub='trainee-sub').count() == 1

    @pytest.mark.usefixtures('rest_auth_enabled', 'pool_lock')
    def test_other_trainees_allocate_their_own(
        self, pool, trainee, other_trainee, training_access_token
    ):
        """Each trainee of the training gets a sandbox."""
        assert self.post(pool, trainee, training_access_token).status_code == 201
        assert self.post(pool, other_trainee, training_access_token).status_code == 201

    @pytest.mark.usefixtures('rest_auth_enabled', 'pool_lock')
    @pytest.mark.parametrize('query', ['?count=2', '?count=0', '?count=abc'])
    def test_count_other_than_one_is_bad_request(self, pool, trainee, training_access_token, query):
        """A trainee cannot allocate more than one sandbox at a time."""
        response = self.post(pool, trainee, training_access_token, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not SandboxAllocationUnit.objects.filter(pool=pool).exists()

    @pytest.mark.usefixtures('rest_auth_enabled', 'pool_lock')
    def test_count_of_one_is_accepted(self, pool, trainee, training_access_token):
        """An explicit count=1, as the training service sends, is fine."""
        assert self.post(pool, trainee, training_access_token, '?count=1').status_code == 201

    @pytest.mark.usefixtures('rest_auth_enabled', 'pool_lock')
    def test_wrong_token_is_forbidden(self, pool, trainee):
        """A token of another training gives no access to this pool."""
        response = self.post(pool, trainee, 'token-of-another-training')

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not SandboxAllocationUnit.objects.filter(pool=pool).exists()

    @pytest.mark.usefixtures('rest_auth_enabled')
    def test_pool_without_a_training_is_bad_request(self, pool, trainee, training_access_token):
        """A pool no training holds cannot be allocated from with a token."""
        response = self.post(pool, trainee, training_access_token)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {'detail': 'The pool is not locked.'}

    @pytest.mark.usefixtures('rest_auth_enabled', 'pool_lock')
    def test_trainee_without_the_token_is_forbidden(self, pool, trainee):
        """Without a token, allocating needs the organizer's model permissions."""
        assert self.post(pool, trainee).status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.usefixtures('rest_auth_enabled', 'pool_lock')
    def test_trainee_cannot_list_the_pool(self, pool, trainee, training_access_token):
        """The token admits a POST only; the pool's units stay hidden from trainees."""
        request = APIRequestFactory().get(
            '/', headers={'X-Training-Access-Token': training_access_token}
        )
        authenticate(request, trainee)

        response = SandboxAllocationUnitListCreateView.as_view()(request, pool_id=pool.id)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.usefixtures('rest_auth_enabled')
    def test_organizer_allocates_without_a_token(self, pool, organizer):
        """An organizer builds sandboxes as before and gets the units as a plain list."""
        response = self.post(pool, organizer, query='?count=2')

        assert response.status_code == status.HTTP_201_CREATED
        assert len(response.data) == 2
        assert all(unit['created_by_sub'] is None for unit in response.data)

    @pytest.mark.usefixtures('pool_lock')
    def test_token_without_authentication_is_bad_request(self, pool, training_access_token):
        """Without REST authentication there is no caller to own the sandbox."""
        response = self.post(pool, token=training_access_token)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'authenticated caller' in response.data['detail']
