"""Live checks of the endpoints a trainee uses to allocate and release their own sandbox.

The tests call a deployed sandbox service over HTTP and are skipped unless these variables are set:

- SANDBOX_API_URL: API root of the sandbox service, ending in the version segment.
- SANDBOX_API_TOKEN: bearer token of an account that holds the administrator role.
- SANDBOX_TRAINEE_SUB: the subject that account is known by to the sandbox service.
- SANDBOX_LOCKED_POOL_ID: a pool locked by a training instance that is not managed and is running.
- SANDBOX_TRAINING_ACCESS_TOKEN: the access token of that training instance.
- SANDBOX_UNLOCKED_POOL_ID: a pool that no training instance locks, and that holds no units.

The account must have no active sandbox of its own, and the locked pool must have room for one.
"""

import os
import time
from collections.abc import Iterator
from typing import Any

import pytest
import requests
from rest_framework import status

API_URL = os.environ.get('SANDBOX_API_URL', '').rstrip('/')
API_TOKEN = os.environ.get('SANDBOX_API_TOKEN', '')
TRAINEE_SUB = os.environ.get('SANDBOX_TRAINEE_SUB', '')
LOCKED_POOL_ID = os.environ.get('SANDBOX_LOCKED_POOL_ID', '')
TRAINING_ACCESS_TOKEN = os.environ.get('SANDBOX_TRAINING_ACCESS_TOKEN', '')
UNLOCKED_POOL_ID = os.environ.get('SANDBOX_UNLOCKED_POOL_ID', '')

TOKEN_HEADER = 'X-Training-Access-Token'
REQUEST_TIMEOUT_SECONDS = 30
STAGE_WAIT_SECONDS = 180
POLL_INTERVAL_SECONDS = 3

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all((
            API_URL,
            API_TOKEN,
            TRAINEE_SUB,
            LOCKED_POOL_ID,
            TRAINING_ACCESS_TOKEN,
            UNLOCKED_POOL_ID,
        )),
        reason='The SANDBOX_* environment variables of the live checks are not set.',
    ),
]


def call(method: str, path: str, **options: Any) -> requests.Response:
    """Send one authenticated request to the sandbox service."""
    headers = {'Authorization': f'Bearer {API_TOKEN}', **options.pop('headers', {})}
    return requests.request(
        method,
        f'{API_URL}/{path.lstrip("/")}',
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
        **options,
    )


def own_active_unit_ids() -> list[int]:
    """The ids of the caller's active units, from the by-creator list."""
    response = call(
        'GET',
        'sandbox-allocation-units/by-creator',
        params={'created_by_sub': TRAINEE_SUB, 'state': 'ACTIVE'},
    )
    assert response.status_code == status.HTTP_200_OK
    return [unit['id'] for unit in response.json()]


def stages_of_allocation(unit_id: int) -> list[str]:
    """The states of the allocation stages of a unit."""
    response = call('GET', f'sandbox-allocation-units/{unit_id}')
    assert response.status_code == status.HTTP_200_OK
    return response.json()['allocation_request']['stages']


def wait_until(condition: Any, description: str) -> None:
    """Poll until the condition holds, failing the test after the wait limit."""
    deadline = time.monotonic() + STAGE_WAIT_SECONDS
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f'Timed out waiting until {description}.')


def release_unit(unit_id: int) -> None:
    """Request the cleanup of a unit, retrying while its first stage still runs."""

    def requested() -> bool:
        response = call('POST', f'sandbox-allocation-units/{unit_id}/cleanup-request?force=true')
        return response.status_code != status.HTTP_400_BAD_REQUEST

    wait_until(requested, f'the cleanup of unit {unit_id} is accepted')


@pytest.fixture(name='created_response', scope='module')
def fixture_created_response() -> Iterator[requests.Response]:
    """The response to the caller's allocation in the locked pool; the unit is released after."""
    response = call(
        'POST',
        f'pools/{LOCKED_POOL_ID}/sandbox-allocation-units',
        headers={TOKEN_HEADER: TRAINING_ACCESS_TOKEN},
    )
    assert response.status_code == status.HTTP_201_CREATED
    yield response
    release_unit(response.json()[0]['id'])


@pytest.fixture(name='own_unit')
def fixture_own_unit(created_response: requests.Response) -> dict[str, Any]:
    """The unit the caller allocated for themselves."""
    return created_response.json()[0]


class TestRefusedAllocation:
    """POST pools/{pool_id}/sandbox-allocation-units that must be turned away."""

    def test_wrong_token_is_forbidden(self):
        """A token that is not the one of the pool's training answers 403."""
        response = call(
            'POST',
            f'pools/{LOCKED_POOL_ID}/sandbox-allocation-units',
            headers={TOKEN_HEADER: 'wrong-token-0000'},
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unlocked_pool_is_bad_request(self):
        """A pool no training holds cannot hand out a sandbox."""
        response = call(
            'POST',
            f'pools/{UNLOCKED_POOL_ID}/sandbox-allocation-units',
            headers={TOKEN_HEADER: TRAINING_ACCESS_TOKEN},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize('count', ['0', '2', 'many'])
    def test_count_other_than_one_is_bad_request(self, count):
        """Allocating for oneself builds exactly one sandbox."""
        response = call(
            'POST',
            f'pools/{LOCKED_POOL_ID}/sandbox-allocation-units',
            params={'count': count},
            headers={TOKEN_HEADER: TRAINING_ACCESS_TOKEN},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_refusals_create_no_unit(self):
        """Refused requests leave the caller's active units as they were."""
        before = own_active_unit_ids()

        call(
            'POST',
            f'pools/{LOCKED_POOL_ID}/sandbox-allocation-units',
            headers={TOKEN_HEADER: 'wrong-token-0000'},
        )
        call(
            'POST',
            f'pools/{LOCKED_POOL_ID}/sandbox-allocation-units',
            params={'count': '2'},
            headers={TOKEN_HEADER: TRAINING_ACCESS_TOKEN},
        )

        assert own_active_unit_ids() == before


class TestTraineeAllocation:
    """POST pools/{pool_id}/sandbox-allocation-units with the training access token."""

    def test_answers_a_plain_list_with_one_owned_unit(self, created_response, own_unit):
        """The 201 body is a plain list holding the new unit and its ownership fields."""
        body = created_response.json()

        assert isinstance(body, list)
        assert len(body) == 1
        assert own_unit['created_by_sub'] == TRAINEE_SUB
        assert {'created_by_sub', 'created_at', 'sandbox_id'} <= set(own_unit)

    @pytest.mark.parametrize('query', [{}, {'count': '1'}])
    def test_second_unit_in_the_pool_conflicts(self, own_unit, query):
        """A caller with an active unit in the pool is answered 409, with or without count."""
        response = call(
            'POST',
            f'pools/{LOCKED_POOL_ID}/sandbox-allocation-units',
            params=query,
            headers={TOKEN_HEADER: TRAINING_ACCESS_TOKEN},
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert own_active_unit_ids() == [own_unit['id']]

    def test_owner_reads_the_unit(self, own_unit):
        """The unit can be retrieved by its id."""
        response = call('GET', f'sandbox-allocation-units/{own_unit["id"]}')

        assert response.status_code == status.HTTP_200_OK
        assert response.json()['id'] == own_unit['id']

    def test_cleanup_while_first_stage_runs_is_bad_request(self, own_unit):
        """A cleanup is refused until the first allocation stage has finished."""
        wait_until(
            lambda: stages_of_allocation(own_unit['id'])[0] == 'RUNNING',
            'the first allocation stage runs',
        )

        response = call('POST', f'sandbox-allocation-units/{own_unit["id"]}/cleanup-request')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unlocking_an_unlocked_unit_succeeds(self, own_unit):
        """Removing a lock that is not there answers 204."""
        wait_until(
            lambda: stages_of_allocation(own_unit['id'])[0] == 'RUNNING',
            'the first allocation stage runs',
        )

        response = call('DELETE', f'sandbox-allocation-units/{own_unit["id"]}/lock')

        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_unlocking_an_unknown_unit_is_not_found(self):
        """A unit that does not exist cannot be unlocked."""
        response = call('DELETE', 'sandbox-allocation-units/999999999/lock')

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.xfail(reason='Reading the lock of a unit without one answers 500.', strict=False)
    def test_reading_a_missing_lock_is_not_a_server_error(self, own_unit):
        """A unit without a lock answers a client error when its lock is read."""
        wait_until(
            lambda: stages_of_allocation(own_unit['id'])[0] == 'RUNNING',
            'the first allocation stage runs',
        )

        response = call('GET', f'sandbox-allocation-units/{own_unit["id"]}/lock')

        assert response.status_code < status.HTTP_500_INTERNAL_SERVER_ERROR


class TestByCreatorList:
    """GET sandbox-allocation-units/by-creator."""

    def test_lists_the_callers_units_as_a_plain_list(self, own_unit):
        """The answer is a plain list, not a page, and holds the caller's unit."""
        response = call(
            'GET',
            'sandbox-allocation-units/by-creator',
            params={'created_by_sub': TRAINEE_SUB, 'state': 'ACTIVE'},
        )

        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.json(), list)
        assert own_unit['id'] in [unit['id'] for unit in response.json()]

    def test_unknown_creator_has_no_units(self):
        """An administrator may query any sub; one without units gets an empty list."""
        response = call(
            'GET',
            'sandbox-allocation-units/by-creator',
            params={'created_by_sub': 'nobody-at-all', 'state': 'ACTIVE'},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []

    @pytest.mark.parametrize('query', [{}, {'created_by_sub': 'someone', 'state': 'NOT_A_STATE'}])
    def test_invalid_query_is_bad_request(self, query):
        """A missing sub or an unknown state is a client error."""
        response = call('GET', 'sandbox-allocation-units/by-creator', params=query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestUnitListSorting:
    """GET pools/{pool_id}/sandbox-allocation-units with sort_by."""

    def test_allocation_unit_id_is_accepted(self):
        """The name the frontend sorts the pool's sandboxes by is accepted."""
        response = call(
            'GET',
            f'pools/{LOCKED_POOL_ID}/sandbox-allocation-units',
            params={'page': 1, 'page_size': 5, 'sort_by': 'allocation_unit_id'},
        )

        assert response.status_code == status.HTTP_200_OK

    def test_unknown_field_is_bad_request(self):
        """An unknown sort field answers 400 instead of failing with a 500."""
        response = call(
            'GET',
            f'pools/{LOCKED_POOL_ID}/sandbox-allocation-units',
            params={'page': 1, 'page_size': 5, 'sort_by': 'no_such_field'},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestPoolMaintenanceAnswers:
    """The bodies of the pool actions, on a pool that holds no units."""

    def test_cancel_allocations_answers_no_units(self):
        """Cancel all allocations answers 201 with no unit results."""
        response = call('POST', f'pools/{UNLOCKED_POOL_ID}/cancel-allocations')

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {'units': []}

    @pytest.mark.parametrize('action', ['cleanup-requests', 'cleanup-unlocked', 'cleanup-failed'])
    def test_cleanup_actions_list_skipped_units(self, action):
        """Each cleanup action answers 201 with the ids of the units it skipped."""
        response = call('POST', f'pools/{UNLOCKED_POOL_ID}/{action}')

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {'skipped_unit_ids': []}
