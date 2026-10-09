"""Tests for the sandbox allocation unit API views."""

from typing import Any

import pytest
from django.contrib.auth import models as auth_models
from rest_framework import status
from rest_framework.test import APIRequestFactory

from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    Sandbox,
    SandboxAllocationUnit,
    SandboxLock,
    StackAllocationStage,
)
from crczp.sandbox_instance_app.tests.conftest import authenticate, set_stage_failed
from crczp.sandbox_instance_app.views import (
    SandboxAllocationUnitByCreatorListView,
    SandboxAllocationUnitDetailUpdateView,
    SandboxAllocationUnitListCreateView,
    SandboxAllocationUnitLockRetrieveCreateDestroyView,
    SandboxCleanupRequestView,
)

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


def call(view: Any, method: str, user: Any, path: str = '/', data: Any = None, **kwargs: Any):
    """Call the view as the user with an authenticated request."""
    request = getattr(APIRequestFactory(), method)(path, data)
    authenticate(request, user)
    return view.as_view()(request, **kwargs)


@pytest.fixture(name='own_unit')
def fixture_own_unit(sandbox_finished, trainee):
    """A unit the trainee allocated for themselves, with its built sandbox."""
    unit = sandbox_finished.allocation_unit
    unit.created_by = trainee
    unit.created_by_sub = 'trainee-sub'
    unit.save()
    return unit


@pytest.mark.usefixtures('rest_auth_enabled')
class TestTraineeOwnUnit:
    """A trainee may read, clean up and unlock the unit they allocated, and no other."""

    def test_owner_reads_the_unit(self, own_unit, trainee):
        """The owner gets the unit, with its sandbox id."""
        response = call(SandboxAllocationUnitDetailUpdateView, 'get', trainee, unit_id=own_unit.id)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['sandbox_id'] == Sandbox.objects.get(allocation_unit=own_unit).id

    def test_other_trainee_cannot_read_the_unit(self, own_unit, other_trainee):
        """Another trainee of the same training gets nothing."""
        response = call(
            SandboxAllocationUnitDetailUpdateView, 'get', other_trainee, unit_id=own_unit.id
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_trainee_cannot_read_an_organizer_unit(self, sandbox, trainee):
        """A unit an organizer built is not a trainee's, even in a locked pool."""
        response = call(
            SandboxAllocationUnitDetailUpdateView,
            'get',
            trainee,
            unit_id=sandbox.allocation_unit.id,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_organizer_reads_any_unit(self, own_unit, organizer):
        """Organizers keep their model permissions."""
        response = call(
            SandboxAllocationUnitDetailUpdateView, 'get', organizer, unit_id=own_unit.id
        )

        assert response.status_code == status.HTTP_200_OK

    def test_owner_cannot_edit_the_unit(self, own_unit, trainee):
        """Editing a unit stays with organizers."""
        response = call(
            SandboxAllocationUnitDetailUpdateView, 'patch', trainee, unit_id=own_unit.id
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_owner_requests_the_cleanup(self, mocker, own_unit, trainee):
        """The owner may tear down their sandbox."""
        create_cleanup_request_force = mocker.patch(
            'crczp.sandbox_instance_app.views.sandbox_requests.create_cleanup_request_force'
        )

        response = call(
            SandboxCleanupRequestView, 'post', trainee, '/?force=true', unit_id=own_unit.id
        )

        assert response.status_code == status.HTTP_201_CREATED
        create_cleanup_request_force.assert_called_once_with(own_unit, delete_pool=False)

    def test_owner_reads_the_cleanup(self, own_unit, trainee):
        """The owner may follow the cleanup; without one the answer is 404."""
        response = call(SandboxCleanupRequestView, 'get', trainee, unit_id=own_unit.id)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_other_trainee_cannot_clean_up(self, mocker, own_unit, other_trainee):
        """Another trainee cannot tear down someone's sandbox."""
        create_cleanup_request = mocker.patch(
            'crczp.sandbox_instance_app.views.sandbox_requests.create_cleanup_request'
        )

        response = call(SandboxCleanupRequestView, 'post', other_trainee, unit_id=own_unit.id)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        create_cleanup_request.assert_not_called()

    def test_owner_cannot_delete_the_cleanup_request(self, own_unit, trainee):
        """Deleting cleanup requests stays with organizers."""
        response = call(SandboxCleanupRequestView, 'delete', trainee, unit_id=own_unit.id)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_owner_unlocks_an_unlocked_sandbox(self, own_unit, trainee):
        """Releasing a trainee's sandbox, which is never locked, succeeds."""
        response = call(
            SandboxAllocationUnitLockRetrieveCreateDestroyView,
            'delete',
            trainee,
            unit_id=own_unit.id,
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_trainee_cannot_unlock_an_organizer_sandbox(self, sandbox_lock, trainee):
        """A sandbox handed out by get-and-lock is released by organizers only."""
        response = call(
            SandboxAllocationUnitLockRetrieveCreateDestroyView,
            'delete',
            trainee,
            unit_id=sandbox_lock.sandbox.allocation_unit.id,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert SandboxLock.objects.filter(pk=sandbox_lock.pk).exists()

    def test_organizer_unlocks_a_sandbox(self, sandbox_lock, organizer):
        """Unlocking still removes the lock."""
        response = call(
            SandboxAllocationUnitLockRetrieveCreateDestroyView,
            'delete',
            organizer,
            unit_id=sandbox_lock.sandbox.allocation_unit.id,
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not SandboxLock.objects.filter(pk=sandbox_lock.pk).exists()


@pytest.mark.usefixtures('rest_auth_enabled')
class TestByCreatorList:
    """GET sandbox-allocation-units/by-creator."""

    @staticmethod
    def ids(response: Any) -> set[int]:
        """The unit ids of a plain-list response."""
        return {unit['id'] for unit in response.data}

    def test_trainee_lists_their_own_units(self, pool, own_unit, trainee, other_trainee):
        """Only the caller's units are listed, as a plain list."""
        SandboxAllocationUnit.objects.create(
            pool=pool, created_by=other_trainee, created_by_sub='other-trainee-sub'
        )

        response = call(
            SandboxAllocationUnitByCreatorListView,
            'get',
            trainee,
            data={'created_by_sub': 'trainee-sub'},
        )

        assert response.status_code == status.HTTP_200_OK
        assert self.ids(response) == {own_unit.id}

    @pytest.mark.parametrize('query', [{'created_by_sub': 'other-trainee-sub'}, {}])
    def test_trainee_cannot_list_others(self, trainee, query):
        """Listing another trainee's units, or omitting the sub, is refused."""
        response = call(SandboxAllocationUnitByCreatorListView, 'get', trainee, data=query)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_same_sub_from_another_issuer_is_not_listed(self, pool, own_unit, trainee):
        """A unit of another identity provider's user with the same sub is not the caller's."""
        impostor = auth_models.User.objects.create(username='trainee-sub|https://other-issuer.test')
        SandboxAllocationUnit.objects.create(
            pool=pool, created_by=impostor, created_by_sub='trainee-sub'
        )

        response = call(
            SandboxAllocationUnitByCreatorListView,
            'get',
            trainee,
            data={'created_by_sub': 'trainee-sub'},
        )

        assert self.ids(response) == {own_unit.id}

    def test_active_state_skips_failed_units(self, pool, own_unit, trainee):
        """state=ACTIVE lists only units that hold or are building a sandbox."""
        failed = SandboxAllocationUnit.objects.create(
            pool=pool, created_by=trainee, created_by_sub='trainee-sub'
        )
        request = AllocationRequest.objects.create(allocation_unit=failed)
        set_stage_failed(
            StackAllocationStage.objects.create(
                allocation_request=request, allocation_request_fk_many=request
            )
        )
        query = {'created_by_sub': 'trainee-sub'}

        everything = call(SandboxAllocationUnitByCreatorListView, 'get', trainee, data=query)
        active = call(
            SandboxAllocationUnitByCreatorListView,
            'get',
            trainee,
            data={**query, 'state': 'ACTIVE'},
        )

        assert self.ids(everything) == {own_unit.id, failed.id}
        assert self.ids(active) == {own_unit.id}

    def test_organizer_lists_any_trainee(self, own_unit, organizer):
        """Organizers may look up any trainee's units."""
        response = call(
            SandboxAllocationUnitByCreatorListView,
            'get',
            organizer,
            data={'created_by_sub': 'trainee-sub'},
        )

        assert self.ids(response) == {own_unit.id}

    @pytest.mark.parametrize('query', [{}, {'created_by_sub': 'x', 'state': 'FINISHED'}])
    def test_invalid_query_is_bad_request(self, organizer, query):
        """A missing sub or an unknown state is a client error."""
        response = call(SandboxAllocationUnitByCreatorListView, 'get', organizer, data=query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
