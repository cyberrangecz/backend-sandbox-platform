"""Tests for creating a unit's cleanup request inside the request that deletes its sandbox."""

from datetime import timedelta
from typing import Any

import django_rq
import fakeredis
import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory
from rq import Queue

from crczp.sandbox_ansible_app.models import (
    NetworkingAnsibleAllocationStage,
    NetworkingAnsibleCleanupStage,
    UserAnsibleAllocationStage,
    UserAnsibleCleanupStage,
)
from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_instance_app.lib import allocation_cancellation, request_handlers, requests
from crczp.sandbox_instance_app.models import (
    ENQUEUE_GRACE,
    AllocationRequest,
    CleanupRequest,
    Pool,
    Sandbox,
    SandboxAllocationUnit,
    SandboxLock,
    StackAllocationStage,
    StackCleanupStage,
)
from crczp.sandbox_instance_app.tests.conftest import set_stage_failed, set_stage_finished
from crczp.sandbox_instance_app.views import (
    PoolCleanupRequestFailedCreateView,
    PoolCleanupRequestsListCreateView,
    PoolCleanupRequestUnlockedCreateView,
    PoolDetailDeleteUpdateView,
    SandboxCleanupRequestView,
)

pytestmark = pytest.mark.django_db


HEALTHY = ['finished', 'finished', 'finished']
FAILED = ['failed', 'failed', 'failed']
PAST_GRACE = ENQUEUE_GRACE + timedelta(minutes=1)
STAGE_SETTERS = {'finished': set_stage_finished, 'failed': set_stage_failed}


def make_unit(
    pool: Pool,
    allocation: list[str] | None = None,
    cleanup: list[str] | None = None,
    locked: bool = False,
) -> SandboxAllocationUnit:
    """
    Create a unit of the pool with its allocation request, its sandbox, and a cleanup request
    when given.

    allocation and cleanup list the states of their three stages in execution order, each
    finished or failed; the allocation defaults to a healthy one. The sandbox is ready when
    every allocation stage finished.
    """
    allocation = allocation or HEALTHY
    unit = SandboxAllocationUnit.objects.create(pool=pool)
    allocation_request = AllocationRequest.objects.create(allocation_unit=unit)
    allocation_stages = [
        StackAllocationStage.objects.create(
            allocation_request=allocation_request, allocation_request_fk_many=allocation_request
        ),
        NetworkingAnsibleAllocationStage.objects.create(
            allocation_request=allocation_request,
            allocation_request_fk_many=allocation_request,
            repo_url='networking-url',
            rev='networking-rev',
        ),
        UserAnsibleAllocationStage.objects.create(
            allocation_request=allocation_request,
            allocation_request_fk_many=allocation_request,
            repo_url='user-url',
            rev='user-rev',
        ),
    ]
    for stage, state in zip(allocation_stages, allocation, strict=True):
        STAGE_SETTERS[state](stage)
    if cleanup is not None:
        cleanup_request = CleanupRequest.objects.create(allocation_unit=unit)
        for stage_class, state in zip(
            (UserAnsibleCleanupStage, NetworkingAnsibleCleanupStage, StackCleanupStage),
            cleanup,
            strict=True,
        ):
            stage = stage_class.objects.create(
                cleanup_request=cleanup_request, cleanup_request_fk_many=cleanup_request
            )
            STAGE_SETTERS[state](stage)
    sandbox = Sandbox.objects.create(
        id=f'sandbox-{unit.id}',
        allocation_unit=unit,
        private_user_key='private-key',
        public_user_key='public-key',
        ready=allocation == HEALTHY,
    )
    if locked:
        SandboxLock.objects.create(sandbox=sandbox)
    pool.size += 1
    pool.save()
    return unit


def queued_cleanup_unit_ids(queue: Queue) -> list[int]:
    """Ids of the units whose cleanup creation job waits in the queue."""
    return [job.args[0].id for job in queue.get_jobs() if job.func_name == '_create_cleanup_jobs']


@pytest.fixture(name='queue', autouse=True)
def fixture_queue(monkeypatch) -> Queue:
    """Serve the default queue and every django_rq connection from one in-memory Redis."""
    connection = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    monkeypatch.setattr(django_rq, 'get_connection', lambda *args, **kwargs: connection)
    queue = Queue(connection=connection)
    monkeypatch.setattr(request_handlers.RequestHandler, 'queue_default', queue)
    return queue


@pytest.fixture(autouse=True)
def sandbox_deletion_checked(monkeypatch) -> None:
    """Fail every sandbox deletion that comes before its unit's cleanup request exists."""
    delete = Sandbox.delete

    def checked_delete(sandbox: Sandbox, *args: Any, **kwargs: Any) -> Any:
        assert CleanupRequest.objects.filter(allocation_unit_id=sandbox.allocation_unit_id).exists()
        return delete(sandbox, *args, **kwargs)

    monkeypatch.setattr(Sandbox, 'delete', checked_delete)


def send(view: Any, method: str, force: bool, **kwargs: Any) -> Any:
    """Send the request to the view, with the force query parameter when set."""
    path = '/?force=true' if force else '/'
    request = getattr(APIRequestFactory(), method)(path)
    return view.as_view()(request, **kwargs)


def age_cleanup_request(unit: SandboxAllocationUnit, age: timedelta) -> None:
    """Move the creation time of the unit's cleanup request into the past."""
    CleanupRequest.objects.filter(allocation_unit=unit).update(created=timezone.now() - age)


def assert_cleanup_requested(unit: SandboxAllocationUnit) -> CleanupRequest:
    """The unit stays, without its sandbox, with a cleanup request that holds no stages."""
    assert SandboxAllocationUnit.objects.filter(pk=unit.id).exists()
    assert not Sandbox.objects.filter(allocation_unit=unit).exists()
    cleanup_request = CleanupRequest.objects.get(allocation_unit=unit)
    assert not cleanup_request.stages.exists()
    return cleanup_request


def make_trainee_unit(pool: Pool, queue: Queue) -> SandboxAllocationUnit:
    """A healthy unit a trainee allocated for themselves an hour ago."""
    unit = make_unit(pool)
    SandboxAllocationUnit.objects.filter(pk=unit.pk).update(
        created_by_sub='trainee-sub', created_at=timezone.now() - timedelta(hours=1)
    )
    return unit


class TestDeletePaths:
    """Every Delete creates the cleanup request in the request and queues its job on commit."""

    @pytest.mark.parametrize(
        ('view', 'method', 'force', 'allocation', 'by_unit'),
        [
            (SandboxCleanupRequestView, 'post', False, None, True),
            (SandboxCleanupRequestView, 'post', True, None, True),
            (PoolCleanupRequestsListCreateView, 'post', False, None, False),
            (PoolCleanupRequestsListCreateView, 'post', True, None, False),
            (PoolCleanupRequestFailedCreateView, 'post', False, FAILED, False),
            (PoolCleanupRequestFailedCreateView, 'post', True, FAILED, False),
            (PoolCleanupRequestUnlockedCreateView, 'post', False, None, False),
            (PoolCleanupRequestUnlockedCreateView, 'post', True, None, False),
            (PoolDetailDeleteUpdateView, 'delete', True, None, False),
        ],
    )
    def test_request_creates_the_cleanup_request_and_commit_queues_its_job(  # pylint: disable=too-many-arguments
        self,
        pool,
        queue,
        django_capture_on_commit_callbacks,
        view,
        method,
        force,
        allocation,
        by_unit,
    ):
        """The row exists before the sandbox goes; the creation job is queued only on commit."""
        unit = make_unit(pool) if allocation is None else make_unit(pool, allocation)
        target = {'unit_id': unit.id} if by_unit else {'pool_id': pool.id}

        with django_capture_on_commit_callbacks() as callbacks:
            response = send(view, method, force, **target)
            assert queued_cleanup_unit_ids(queue) == []

        assert response.status_code == status.HTTP_201_CREATED
        assert_cleanup_requested(unit)
        for callback in callbacks:
            callback()
        assert queued_cleanup_unit_ids(queue) == [unit.id]

    def test_pool_deletion_queues_a_cleanup_that_deletes_the_pool(
        self, pool, queue, django_capture_on_commit_callbacks
    ):
        """Deleting a pool by force queues the cleanup that removes the pool once it is empty."""
        make_unit(pool)

        with django_capture_on_commit_callbacks(execute=True):
            send(PoolDetailDeleteUpdateView, 'delete', True, pool_id=pool.id)

        [job] = queue.get_jobs()
        assert job.instance.delete_pool

    def test_locked_sandbox_is_unlocked_and_deleted_by_force(
        self, pool, queue, django_capture_on_commit_callbacks
    ):
        """A forced Delete removes the lock with the sandbox, after the cleanup request."""
        unit = make_unit(pool, locked=True)

        with django_capture_on_commit_callbacks(execute=True):
            send(SandboxCleanupRequestView, 'post', True, unit_id=unit.id)

        assert_cleanup_requested(unit)
        assert not SandboxLock.objects.exists()
        assert queued_cleanup_unit_ids(queue) == [unit.id]

    def test_trainee_cleanup_creates_the_cleanup_request(
        self, pool, queue, django_capture_on_commit_callbacks
    ):
        """The trainee sandbox cleanup creates the row at once and queues the job on commit."""
        unit = make_trainee_unit(pool, queue)

        with django_capture_on_commit_callbacks() as callbacks:
            report = requests.cleanup_expired_trainee_units(timezone.now())
            assert queued_cleanup_unit_ids(queue) == []

        assert report.cleaned == [unit.id]
        assert_cleanup_requested(unit)
        for callback in callbacks:
            callback()
        assert queued_cleanup_unit_ids(queue) == [unit.id]

    def test_failed_cleanup_is_restarted_on_its_request(
        self, pool, queue, django_capture_on_commit_callbacks
    ):
        """A forced Delete of a unit whose cleanup failed empties and reuses its request."""
        unit = make_unit(pool, cleanup=FAILED)
        cleanup_request_id = unit.cleanup_request.id
        age_cleanup_request(unit, PAST_GRACE)

        with django_capture_on_commit_callbacks(execute=True):
            send(SandboxCleanupRequestView, 'post', True, unit_id=unit.id)

        cleanup_request = assert_cleanup_requested(unit)
        assert cleanup_request.id == cleanup_request_id
        assert timezone.now() - cleanup_request.created < ENQUEUE_GRACE
        assert queued_cleanup_unit_ids(queue) == [unit.id]


class TestQueuedCleanupIsNotQueuedAgain:
    """A cleanup request without stages counts as queued, so it gets no second job or row."""

    @pytest.fixture(name='queued_unit')
    def fixture_queued_unit(self, pool, queue, django_capture_on_commit_callbacks):
        """A trainee unit deleted with its creation job queued."""
        unit = make_trainee_unit(pool, queue)
        with django_capture_on_commit_callbacks(execute=True):
            send(SandboxCleanupRequestView, 'post', False, unit_id=unit.id)
        return unit

    def test_forced_delete_queues_nothing(
        self, queue, django_capture_on_commit_callbacks, queued_unit
    ):
        """A second forced Delete leaves the queued cleanup alone."""
        with django_capture_on_commit_callbacks(execute=True):
            response = send(SandboxCleanupRequestView, 'post', True, unit_id=queued_unit.id)

        assert response.status_code == status.HTTP_201_CREATED
        assert queued_cleanup_unit_ids(queue) == [queued_unit.id]
        assert CleanupRequest.objects.filter(allocation_unit=queued_unit).count() == 1

    def test_plain_delete_is_refused(self, queue, queued_unit):
        """A second plain Delete is refused, naming the existing cleanup request."""
        response = send(SandboxCleanupRequestView, 'post', False, unit_id=queued_unit.id)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert queued_cleanup_unit_ids(queue) == [queued_unit.id]

    def test_trainee_cleanup_leaves_it_alone(
        self, queue, django_capture_on_commit_callbacks, queued_unit
    ):
        """The trainee cleanup neither queues it again nor reports it."""
        with django_capture_on_commit_callbacks(execute=True):
            report = requests.cleanup_expired_trainee_units(timezone.now())

        assert report == requests.TraineeCleanupReport()
        assert queued_cleanup_unit_ids(queue) == [queued_unit.id]

    def test_its_deletion_is_refused(self, queued_unit):
        """The cleanup request of a queued cleanup cannot be deleted."""
        response = send(SandboxCleanupRequestView, 'delete', False, unit_id=queued_unit.id)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert CleanupRequest.objects.filter(allocation_unit=queued_unit).exists()

    def test_finished_cleanup_request_is_deleted(self, pool, queue):
        """The cleanup request of a failed cleanup can be deleted."""
        unit = make_unit(pool, cleanup=FAILED)

        response = send(SandboxCleanupRequestView, 'delete', False, unit_id=unit.id)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not CleanupRequest.objects.filter(allocation_unit=unit).exists()

    def test_unit_keeps_counting_as_active(self, queued_unit):
        """A trainee whose sandbox waits for its cleanup still holds it."""
        assert SandboxAllocationUnit.objects.filter(
            SandboxAllocationUnit.is_active(), pk=queued_unit.pk
        ).exists()


class TestCreationJob:
    """The queued job creates the stages on the cleanup request the Delete created."""

    def test_reuses_the_request_a_stale_unit_does_not_know(self, pool, queue):
        """A unit read before its request was created still gets that request's stages."""
        unit = make_unit(pool)
        stale_unit = SandboxAllocationUnit.objects.get(pk=unit.pk)
        assert not hasattr(stale_unit, 'cleanup_request')
        cleanup_request = CleanupRequest.objects.create(allocation_unit=unit)

        request_handlers.CleanupRequestHandler()._create_cleanup_jobs(stale_unit)

        assert CleanupRequest.objects.filter(allocation_unit=unit).count() == 1
        assert cleanup_request.stages.count() == 3


class TestDeletingAMissingCleanupRequest:
    """A unit that has no cleanup request has nothing to delete."""

    def test_answers_not_found(self, pool):
        """Deleting the cleanup request of a unit without one is answered with 404."""
        unit = make_unit(pool)

        response = send(SandboxCleanupRequestView, 'delete', False, unit_id=unit.id)

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestForcedPoolDeletion:
    """A forced pool deletion removes the pool whatever state its units are in."""

    def test_a_pool_without_units_is_deleted_whatever_its_size_says(self, pool):
        """A size that drifted above the unit count does not keep the pool."""
        Pool.objects.filter(pk=pool.pk).update(size=2)

        response = send(PoolDetailDeleteUpdateView, 'delete', True, pool_id=pool.id)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Pool.objects.filter(pk=pool.pk).exists()

    def test_a_unit_with_a_running_cleanup_makes_that_cleanup_delete_the_pool(self, pool):
        """The cleanup that was queued earlier carries the pool deletion along."""
        unit = make_unit(pool, cleanup=FAILED)
        StackCleanupStage.objects.filter(cleanup_request__allocation_unit=unit).update(
            finished=False, failed=False
        )

        send(PoolDetailDeleteUpdateView, 'delete', True, pool_id=pool.id)

        assert CleanupRequest.objects.get(allocation_unit=unit).delete_pool


class TestCancellingAnAllocationWithABlockedCleanup:
    """A cancel that could not be followed by its cleanup is refused before it stops anything."""

    def test_a_locked_sandbox_refuses_the_cancel_and_stops_nothing(self, pool):
        """The build keeps running when the cleanup that must follow the cancel would be refused."""
        unit = make_unit(pool, locked=True)
        UserAnsibleAllocationStage.objects.filter(allocation_request__allocation_unit=unit).update(
            finished=False
        )

        with pytest.raises(exceptions.ValidationError, match='locked'):
            requests.cancel_allocation_request_and_clean_up(
                AllocationRequest.objects.get(allocation_unit=unit)
            )

        stage = UserAnsibleAllocationStage.objects.get(allocation_request__allocation_unit=unit)
        assert not stage.finished

    def test_a_queued_allocation_whose_unit_has_a_cleanup_is_refused_at_once(
        self, pool, monkeypatch
    ):
        """No wait for stages that never come: the unit already belongs to a cleanup."""
        unit = SandboxAllocationUnit.objects.create(pool=pool)
        allocation_request = AllocationRequest.objects.create(allocation_unit=unit)
        CleanupRequest.objects.create(allocation_unit=unit)
        monkeypatch.setattr(allocation_cancellation.time, 'sleep', fail_on_wait)

        with pytest.raises(exceptions.ValidationError, match='cleanup request'):
            requests.cancel_allocation_request_and_clean_up(allocation_request)


class TestCancellingAnAllocationThatAlreadyEnded:
    """A cancel retried after the build ended still queues the cleanup that is missing."""

    def test_a_failed_allocation_without_a_cleanup_gets_its_cleanup(self, mocker, pool):
        """The retry of a cancel whose cleanup step failed goes straight to the cleanup."""
        unit = make_unit(pool, allocation=FAILED)
        create_cleanup = mocker.patch.object(requests, 'create_cleanup_request')

        stop_pending = requests.cancel_allocation_request_and_clean_up(
            AllocationRequest.objects.get(allocation_unit=unit)
        )

        assert stop_pending is False
        create_cleanup.assert_called_once()

    def test_a_finished_allocation_is_still_refused(self, mocker, pool):
        """A build that succeeded is not cancelled and not cleaned up."""
        unit = make_unit(pool)
        create_cleanup = mocker.patch.object(requests, 'create_cleanup_request')

        with pytest.raises(exceptions.ValidationError, match='does not need cancelling'):
            requests.cancel_allocation_request_and_clean_up(
                AllocationRequest.objects.get(allocation_unit=unit)
            )

        create_cleanup.assert_not_called()

    def test_a_cleanup_queued_by_another_process_meanwhile_counts_as_success(self, mocker, pool):
        """The cleanup request that appeared while the stop was awaited is not an error."""
        unit = make_unit(pool, allocation=FAILED)

        def queue_cleanup_first(allocation_unit: SandboxAllocationUnit) -> None:
            CleanupRequest.objects.create(allocation_unit=allocation_unit)
            raise exceptions.ValidationError('already has a cleanup request')

        mocker.patch.object(requests, 'create_cleanup_request', side_effect=queue_cleanup_first)

        stop_pending = requests.cancel_allocation_request_and_clean_up(
            AllocationRequest.objects.get(allocation_unit=unit)
        )

        assert stop_pending is False

    def test_a_cleanup_refusal_without_a_cleanup_request_is_still_an_error(self, mocker, pool):
        """Only an existing cleanup request turns the refusal into success."""
        unit = make_unit(pool, allocation=FAILED)
        mocker.patch.object(
            requests, 'create_cleanup_request', side_effect=exceptions.ValidationError('refused')
        )

        with pytest.raises(exceptions.ValidationError, match='refused'):
            requests.cancel_allocation_request_and_clean_up(
                AllocationRequest.objects.get(allocation_unit=unit)
            )


def fail_on_wait(_seconds: float) -> None:
    """Stand-in for sleep that fails the test."""
    pytest.fail('The cancel waited for stages that never come.')
