"""Tests for Cancel all allocations and the cancel of a queued allocation request."""

from typing import Any

import django_rq
import fakeredis
import pytest
from django.contrib.auth.models import Group, User
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate
from rq import Queue
from rq.job import Job, JobStatus

from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_instance_app.lib import allocation_cancellation, request_handlers, requests
from crczp.sandbox_instance_app.lib.allocation_cancellation import (
    CancellationAction,
    UnitCancellation,
)
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    AllocationRQJob,
    CleanupRequest,
    Pool,
    SandboxAllocationUnit,
    StackAllocationStage,
    StackCleanupStage,
)
from crczp.sandbox_instance_app.tests.conftest import (
    set_stage_failed,
    set_stage_finished,
    set_stage_started,
)
from crczp.sandbox_instance_app.views import PoolCancelAllocationsView

pytestmark = pytest.mark.django_db

ORGANIZER_ROLES = ['ROLE_SANDBOX-SERVICE_ORGANIZER']
ADMIN_ROLES = ['ROLE_SANDBOX-SERVICE_ADMIN']
TRAINEE_ROLES = ['ROLE_SANDBOX-SERVICE_TRAINEE']


@pytest.fixture(name='queue', autouse=True)
def fixture_queue(monkeypatch) -> Queue:
    """Serve the creation job queue and every django_rq connection from one in-memory Redis."""
    connection = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    monkeypatch.setattr(django_rq, 'get_connection', lambda *args, **kwargs: connection)
    queue = Queue(connection=connection)
    monkeypatch.setattr(request_handlers.RequestHandler, 'queue_default', queue)
    return queue


@pytest.fixture(name='cancel_requests')
def fixture_cancel_requests(mocker):
    """The reliable stop of the builds, which needs live workers; every stop is confirmed."""
    return mocker.patch.object(
        request_handlers,
        'cancel_requests',
        side_effect=lambda requests_to_cancel, wait_seconds: [False] * len(requests_to_cancel),
    )


@pytest.fixture(name='create_cleanup_request')
def fixture_create_cleanup_request(mocker):
    """The normal cleanup, which talks to NetBird and enqueues jobs."""
    return mocker.patch.object(requests, 'create_cleanup_request')


def make_unit(pool: Pool, stack_stage: str | None = None, with_job: bool = True) -> Any:
    """Create a unit of the pool and its allocation request.

    stack_stage is None (no stages yet), 'queued', 'running', 'finished' or 'failed'; a stage
    carries an RQ job row when with_job is set.
    """
    unit = SandboxAllocationUnit.objects.create(pool=pool)
    allocation_request = AllocationRequest.objects.create(allocation_unit=unit)
    if stack_stage is not None:
        stage = StackAllocationStage.objects.create(
            allocation_request=allocation_request, allocation_request_fk_many=allocation_request
        )
        {
            'queued': lambda _: None,
            'running': set_stage_started,
            'finished': set_stage_finished,
            'failed': set_stage_failed,
        }[stack_stage](stage)
        if with_job:
            AllocationRQJob.objects.create(job_id=f'job-{unit.id}', allocation_stage=stage)
    pool.size += 1
    pool.save()
    return unit


def enqueue_creation_job(queue: Queue, units: list[Any]) -> Job:
    """Queue the job that creates the stages of the units, as an allocation does."""
    handler = request_handlers.AllocationRequestHandler()
    return queue.enqueue(
        handler._create_allocation_jobs,  # pylint: disable=protected-access
        units,
        None,
    )


def remaining(pool: Pool) -> set[int]:
    """Ids of the pool's units still in the database."""
    return set(SandboxAllocationUnit.objects.filter(pool=pool).values_list('id', flat=True))


def cancel_all(pool: Pool) -> dict[int, UnitCancellation]:
    """Run Cancel all allocations without waiting for unconfirmed stops; key results by unit id."""
    results = allocation_cancellation.cancel_pool_allocations(pool, wait_seconds=0)
    return {result.unit_id: result for result in results}


class TestQueuedUnits:
    """Units whose creation job has not built them."""

    def test_job_is_cancelled_in_redis_and_units_are_removed(self, pool, queue):
        """The queued job is removed and reads back as cancelled; units and size go."""
        first, second = make_unit(pool), make_unit(pool)
        job = enqueue_creation_job(queue, [first, second])

        results = cancel_all(pool)

        assert queue.count == 0
        assert job.get_status(refresh=True) == JobStatus.CANCELED
        assert remaining(pool) == set()
        pool.refresh_from_db()
        assert pool.size == 0
        assert set(results) == {first.id, second.id}
        for result in results.values():
            assert result.action == CancellationAction.REMOVED
            assert result.stack_name == f'{pool.get_pool_prefix()}-s{result.unit_id:010d}'

    def test_job_shared_with_other_units_stays_queued(self, pool, queue, definition):
        """A job that also builds a unit outside the cancelled set is left alone."""
        other_pool = Pool.objects.create(
            definition=definition,
            max_size=3,
            private_management_key='key',
            public_management_key='key',
            uuid='other-pool',
        )
        own, foreign = make_unit(pool), make_unit(other_pool)
        job = enqueue_creation_job(queue, [own, foreign])

        items = allocation_cancellation.cancel_units([own], [], 0)

        assert queue.count == 1
        assert job.get_status(refresh=True) == JobStatus.QUEUED
        assert remaining(pool) == set()
        assert remaining(other_pool) == {foreign.id}
        assert [result.unit_id for result in items] == [own.id]

    def test_unit_whose_job_is_gone_is_removed(self, pool):
        """No stages and no job in the queue: nothing was built, the unit goes."""
        unit = make_unit(pool)

        results = cancel_all(pool)

        assert remaining(pool) == set()
        assert results[unit.id].action == CancellationAction.REMOVED

    def test_job_started_meanwhile_stops_the_unit_like_a_running_build(
        self, pool, cancel_requests, create_cleanup_request
    ):
        """A unit that got its stages after it was listed as queued is stopped, then cleaned up."""
        unit = make_unit(pool)
        stage = StackAllocationStage.objects.create(
            allocation_request=unit.allocation_request,
            allocation_request_fk_many=unit.allocation_request,
        )
        AllocationRQJob.objects.create(job_id='job-started', allocation_stage=stage)

        items = allocation_cancellation.cancel_units([unit], [], 0)

        assert remaining(pool) == {unit.id}
        [stopped_request] = cancel_requests.call_args.args[0]
        assert stopped_request.allocation_unit_id == unit.id
        create_cleanup_request.assert_called_once()
        assert [(result.action, result.stop_pending) for result in items] == [
            (CancellationAction.STOPPED, False)
        ]

    def test_unit_whose_stage_jobs_never_appear_fails(self, pool, cancel_requests):
        """Stages without jobs after the wait: the unit is a failed item and nothing is stopped."""
        unit = make_unit(pool, 'queued', with_job=False)

        items = allocation_cancellation.cancel_units([unit], [], 0)

        assert [result.action for result in items] == [CancellationAction.FAILED]
        assert items[0].reason
        assert remaining(pool) == {unit.id}
        cancel_requests.assert_called_once_with([], 0)

    def test_job_with_unreadable_arguments_fails_the_queued_units(self, pool, queue):
        """A queued job whose arguments cannot be loaded fails each queued unit, not the run."""
        unit = make_unit(pool)
        job = enqueue_creation_job(queue, [unit])
        job.connection.hset(job.key, 'data', 'not a pickle')

        results = cancel_all(pool)

        result = results[unit.id]
        assert result.action == CancellationAction.FAILED
        assert 'could not be read' in result.reason
        assert remaining(pool) == {unit.id}
        assert queue.count == 1


class TestRunningBuilds:
    """Units with an unfinished allocation stage."""

    def test_request_finished_meanwhile_is_a_skipped_item(
        self, pool, cancel_requests, create_cleanup_request
    ):
        """A request that finished after the unit was listed is skipped; the run still finishes."""
        finished, running = make_unit(pool, 'running'), make_unit(pool, 'running')
        cancel_requests.side_effect = None
        cancel_requests.return_value = [None, False]

        results = cancel_all(pool)

        assert results[finished.id].action == CancellationAction.SKIPPED
        assert results[finished.id].reason
        assert results[running.id].action == CancellationAction.STOPPED
        create_cleanup_request.assert_called_once()

    def test_confirmed_stop_is_followed_by_the_cleanup(
        self, pool, cancel_requests, create_cleanup_request
    ):
        """Every build is stopped in one call; each confirmed unit gets its cleanup."""
        running, waiting = make_unit(pool, 'running'), make_unit(pool, 'queued')

        results = cancel_all(pool)

        requests_to_cancel = cancel_requests.call_args.args[0]
        assert {request.allocation_unit_id for request in requests_to_cancel} == {
            running.id,
            waiting.id,
        }
        assert {call.args[0].id for call in create_cleanup_request.call_args_list} == {
            running.id,
            waiting.id,
        }
        for result in results.values():
            assert result.action == CancellationAction.STOPPED
            assert not result.stop_pending

    def test_failed_build_with_a_pending_netbird_job_is_stopped_and_cleaned_up(
        self, pool, cancel_requests, create_cleanup_request
    ):
        """A failed build whose NetBird job is still tracked is cancelled; a ready unit is not."""
        failed = make_unit(pool, 'failed')
        ready = make_unit(pool, 'finished')
        AllocationRequest.objects.update(netbird_job_id='netbird')

        results = cancel_all(pool)

        assert set(results) == {failed.id}
        assert [request.allocation_unit_id for request in cancel_requests.call_args.args[0]] == [
            failed.id
        ]
        create_cleanup_request.assert_called_once()
        assert ready.id in remaining(pool)

    def test_unconfirmed_stop_is_stop_pending_without_cleanup(
        self, pool, cancel_requests, create_cleanup_request
    ):
        """A stop that is not confirmed in time is stopped with the stop pending."""
        unit = make_unit(pool, 'running')
        cancel_requests.side_effect = None
        cancel_requests.return_value = [True]

        results = cancel_all(pool)

        result = results[unit.id]
        assert (result.action, result.stop_pending) == (CancellationAction.STOPPED, True)
        create_cleanup_request.assert_not_called()

    def test_cleanup_that_cannot_be_queued_is_a_failed_item(
        self, pool, cancel_requests, create_cleanup_request
    ):
        """A stopped unit whose cleanup is refused, e.g. a locked sandbox, fails with the reason."""
        make_unit(pool, 'running')
        create_cleanup_request.side_effect = exceptions.ValidationError('Sandbox is locked.')

        results = cancel_all(pool)

        [result] = results.values()
        assert result.action == CancellationAction.FAILED
        assert 'Sandbox is locked.' in result.reason

    def test_an_error_of_the_stop_propagates(self, pool, cancel_requests):
        """An error of the stop is raised to the caller instead of being reported per unit."""
        make_unit(pool, 'running')
        cancel_requests.side_effect = RuntimeError('redis down')

        with pytest.raises(RuntimeError, match='redis down'):
            cancel_all(pool)


class TestUnitsLeftAlone:
    """Units that are not cancelled and are not in the result."""

    def test_ready_failed_and_cleaning_units_are_untouched(
        self, pool, cancel_requests, create_cleanup_request
    ):
        """Ready sandboxes, failed allocations and units being cleaned up stay as they are."""
        ready = make_unit(pool, 'finished')
        failed = make_unit(pool, 'failed')
        cleaning = make_unit(pool, 'finished')
        cleanup_request = CleanupRequest.objects.create(allocation_unit=cleaning)
        cleanup_stage = StackCleanupStage.objects.create(
            cleanup_request=cleanup_request, cleanup_request_fk_many=cleanup_request
        )
        set_stage_started(cleanup_stage)
        building = make_unit(pool, 'running')

        results = cancel_all(pool)

        assert set(results) == {building.id}
        assert {ready.id, failed.id, cleaning.id} <= remaining(pool)
        assert [request.allocation_unit_id for request in cancel_requests.call_args.args[0]] == [
            building.id
        ]

    def test_queued_unit_being_cleaned_up_is_untouched(self, pool):
        """A unit with a cleanup request is never removed as queued."""
        unit = make_unit(pool)
        CleanupRequest.objects.create(allocation_unit=unit)

        results = cancel_all(pool)

        assert remaining(pool) == {unit.id}
        assert not results


class TestCancelOfQueuedRequest:
    """PATCH allocation-requests/{id}/cancel for a request whose stages do not exist yet."""

    def test_removes_the_queued_job_and_unit(self, pool, queue):
        """The job is cancelled in Redis and the unit goes, with no stop pending."""
        unit = make_unit(pool)
        job = enqueue_creation_job(queue, [unit])

        stop_pending = requests.cancel_allocation_request_and_clean_up(unit.allocation_request)

        assert stop_pending is False
        assert job.get_status(refresh=True) == JobStatus.CANCELED
        assert remaining(pool) == set()
        pool.refresh_from_db()
        assert pool.size == 0

    def test_failure_is_a_validation_error(self, mocker, pool, cancel_requests):
        """A unit that cannot be cancelled answers 400, not a server error."""
        mocker.patch.object(allocation_cancellation, 'STOP_CONFIRMATION_SECONDS', 0)
        unit = make_unit(pool, 'queued', with_job=False)

        with pytest.raises(exceptions.ValidationError):
            allocation_cancellation.cancel_queued_request(unit.allocation_request)


@pytest.mark.usefixtures('rest_auth_enabled')
class TestPoolCancelAllocationsView:
    """POST pools/{pool_id}/cancel-allocations."""

    @staticmethod
    def post(roles: list[str], pool_id: int) -> Any:
        """POST to the view as a caller in the given UAG role groups."""
        user = User.objects.create(username='requester')
        user.groups.set(Group.objects.get_or_create(name=role)[0] for role in roles)
        request = APIRequestFactory().post('/')
        force_authenticate(request, user=user)
        return PoolCancelAllocationsView.as_view()(request, pool_id=pool_id)

    @pytest.mark.parametrize(
        ('roles', 'expected_status'),
        [
            (ORGANIZER_ROLES, status.HTTP_201_CREATED),
            (ADMIN_ROLES, status.HTTP_201_CREATED),
            (TRAINEE_ROLES, status.HTTP_403_FORBIDDEN),
        ],
    )
    def test_permissions(self, pool, roles, expected_status):
        """Organizers and admins cancel; trainees are refused."""
        assert self.post(roles, pool.id).status_code == expected_status

    def test_answers_one_result_per_unit(self, pool):
        """The answer lists the affected units with what was done to each."""
        unit = make_unit(pool)

        response = self.post(ORGANIZER_ROLES, pool.id)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data == {
            'units': [
                {
                    'unit_id': unit.id,
                    'stack_name': unit.get_stack_name(),
                    'action': 'removed',
                    'stop_pending': False,
                    'reason': '',
                }
            ]
        }

    def test_unknown_pool_is_not_found(self):
        """A pool that does not exist answers 404."""
        assert self.post(ADMIN_ROLES, 999).status_code == status.HTTP_404_NOT_FOUND


class TestCancellationOverlappingAndTimeBudget:
    """A unit listed twice is handled once, and one wait budget covers the whole call."""

    def test_a_unit_listed_as_queued_and_as_building_is_reported_once(
        self, pool, cancel_requests, create_cleanup_request
    ):
        """The building list owns the unit; the queued list does not fail it again."""
        unit = make_unit(pool, 'running', with_job=False)

        results = allocation_cancellation.cancel_units([unit], [unit], 0)

        assert [result.unit_id for result in results] == [unit.id]
        assert results[0].action != CancellationAction.FAILED

    def test_the_stop_wait_gets_what_the_stage_job_wait_left(
        self, mocker, pool, cancel_requests, create_cleanup_request
    ):
        """The time spent waiting for stage jobs is taken off the wait for the stops."""
        clock = mocker.patch.object(allocation_cancellation, 'time')
        clock.monotonic.side_effect = [100.0, 108.0, 108.0, 112.0]
        unit = make_unit(pool, 'running')

        allocation_cancellation.cancel_units([], [unit], 20)

        assert cancel_requests.call_args.args[1] == 8
