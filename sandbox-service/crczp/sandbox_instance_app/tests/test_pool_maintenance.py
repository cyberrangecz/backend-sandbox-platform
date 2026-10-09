"""Tests for the pool maintenance actions that remove stuck allocation units."""

from typing import Any

import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory

from crczp.sandbox_instance_app.lib import requests
from crczp.sandbox_instance_app.lib.requests import StuckUnits
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    CleanupRequest,
    Pool,
    Sandbox,
    SandboxAllocationUnit,
    SandboxLock,
    StackAllocationStage,
    StackCleanupStage,
)
from crczp.sandbox_instance_app.tests.conftest import (
    set_stage_failed,
    set_stage_finished,
    set_stage_started,
)
from crczp.sandbox_instance_app.views import (
    PoolCancelQueuedView,
    PoolForceCancelAllocationView,
    PoolForceCleanupView,
)

pytestmark = pytest.mark.django_db


def make_unit(pool: Pool, stack_stage: str | None = None, with_sandbox: bool = False) -> Any:
    """Create a unit of the pool, its stack stage in the given state, and its sandbox.

    stack_stage is None (no stages yet), 'queued', 'running', 'finished' or 'failed'.
    """
    unit = SandboxAllocationUnit.objects.create(pool=pool)
    request = AllocationRequest.objects.create(allocation_unit=unit)
    if stack_stage is not None:
        stage = StackAllocationStage.objects.create(
            allocation_request=request, allocation_request_fk_many=request
        )
        {
            'queued': lambda _: None,
            'running': set_stage_started,
            'finished': set_stage_finished,
            'failed': set_stage_failed,
        }[stack_stage](stage)
    if with_sandbox:
        Sandbox.objects.create(
            id=f'sandbox-{unit.id}',
            allocation_unit=unit,
            private_user_key='private-key',
            public_user_key='public-key',
        )
    pool.size += 1
    pool.save()
    return unit


def add_cleanup(unit: Any, finished: bool) -> None:
    """Give the unit a cleanup request whose stack stage is running or finished."""
    request = CleanupRequest.objects.create(allocation_unit=unit)
    stage = StackCleanupStage.objects.create(
        cleanup_request=request, cleanup_request_fk_many=request
    )
    (set_stage_finished if finished else set_stage_started)(stage)


def remaining(pool: Pool) -> set[int]:
    """Ids of the pool's units still in the database."""
    return set(SandboxAllocationUnit.objects.filter(pool=pool).values_list('id', flat=True))


@pytest.fixture(name='handlers')
def fixture_handlers(mocker):
    """The request handlers, which cancel RQ jobs and stage processes."""
    return {
        'allocation': mocker.patch(
            'crczp.sandbox_instance_app.lib.requests.request_handlers.AllocationRequestHandler'
        ),
        'cleanup': mocker.patch(
            'crczp.sandbox_instance_app.lib.requests.request_handlers.CleanupRequestHandler'
        ),
    }


@pytest.fixture(name='destroy_netbird')
def fixture_destroy_netbird(mocker):
    """The NetBird teardown, which talks to NetBird."""
    return mocker.patch(
        'crczp.sandbox_instance_app.lib.requests.netbird.destroy_netbird_for_sandbox'
    )


@pytest.mark.usefixtures('destroy_netbird')
class TestForceRemoveUnits:
    """requests.force_remove_units."""

    def test_queued_removes_units_whose_allocation_has_not_started(self, pool, handlers):
        """Units before the job created stages, and with no stage started, go."""
        no_stages = make_unit(pool)
        not_started = make_unit(pool, 'queued', with_sandbox=True)
        running = make_unit(pool, 'running', with_sandbox=True)
        built = make_unit(pool, 'finished', with_sandbox=True)

        removed = requests.force_remove_units(pool, StuckUnits.QUEUED)

        assert removed == 2
        assert remaining(pool) == {running.id, built.id}
        assert not Sandbox.objects.filter(pk=f'sandbox-{not_started.id}').exists()
        cancel = handlers['allocation'].return_value.cancel_request
        assert {c.args[0].allocation_unit_id for c in cancel.call_args_list} == {
            no_stages.id,
            not_started.id,
        }
        pool.refresh_from_db()
        assert pool.size == 2

    def test_queued_skips_cancelled_and_cleaned_up_units(self, pool):
        """A cancelled allocation and a unit being cleaned up are not queued."""
        cancelled = make_unit(pool, 'queued')
        stage = StackAllocationStage.objects.get(allocation_request__allocation_unit=cancelled)
        stage.finished, stage.failed = True, True
        stage.save()
        cleaning = make_unit(pool)
        add_cleanup(cleaning, finished=False)

        assert requests.force_remove_units(pool, StuckUnits.QUEUED) == 0
        assert remaining(pool) == {cancelled.id, cleaning.id}

    def test_first_stage_running_removes_the_unit_and_its_sandbox(
        self, pool, handlers, destroy_netbird
    ):
        """A hung stack stage is cancelled; the sandbox, its lock and NetBird go too."""
        running = make_unit(pool, 'running', with_sandbox=True)
        sandbox = Sandbox.objects.get(allocation_unit=running)
        SandboxLock.objects.create(sandbox=sandbox)
        failed = make_unit(pool, 'failed', with_sandbox=True)

        removed = requests.force_remove_units(pool, StuckUnits.FIRST_STAGE_RUNNING)

        assert removed == 1
        assert remaining(pool) == {failed.id}
        assert not Sandbox.objects.filter(pk=sandbox.pk).exists()
        destroy_netbird.assert_called_once_with(sandbox)
        handlers['allocation'].return_value.cancel_request.assert_called_once()

    def test_cleanup_unfinished_removes_units_with_a_hung_cleanup(self, pool, handlers):
        """Units whose cleanup runs are cancelled and removed; finished cleanups stay."""
        hung = make_unit(pool, 'finished')
        add_cleanup(hung, finished=False)
        done = make_unit(pool, 'finished')
        add_cleanup(done, finished=True)
        built = make_unit(pool, 'finished', with_sandbox=True)

        removed = requests.force_remove_units(pool, StuckUnits.CLEANUP_UNFINISHED)

        assert removed == 1
        assert remaining(pool) == {done.id, built.id}
        handlers['cleanup'].return_value.cancel_request.assert_called_once()

    def test_pool_size_never_goes_negative(self, pool):
        """A pool whose size is already off does not drop below zero."""
        make_unit(pool)
        Pool.objects.filter(pk=pool.pk).update(size=0)

        requests.force_remove_units(pool, StuckUnits.QUEUED)

        pool.refresh_from_db()
        assert pool.size == 0

    def test_other_pools_are_untouched(self, pool, definition, created_by):
        """Only the given pool's units are removed."""
        other_pool = Pool.objects.create(
            definition=definition,
            max_size=3,
            private_management_key='key',
            public_management_key='key',
            uuid='other',
            created_by=created_by,
        )
        other = make_unit(other_pool)

        requests.force_remove_units(pool, StuckUnits.QUEUED)

        assert remaining(other_pool) == {other.id}


@pytest.mark.usefixtures('rest_auth_enabled', 'destroy_netbird', 'handlers')
class TestPoolMaintenanceViews:
    """POST pools/{pool_id}/cancel-queued, force-cancel-allocation and force-cleanup."""

    @staticmethod
    def post(mocker, view: Any, roles: list[str], pool_id: int) -> Any:
        """POST to the view as a caller with the given UAG roles."""
        mocker.patch(
            'crczp.sandbox_uag.permissions.authenticator_class.get_bearer_token',
            return_value=b'token',
        )
        mocker.patch('crczp.sandbox_uag.permissions.get_user_roles', return_value=roles)
        return view.as_view()(APIRequestFactory().post('/'), pool_id=pool_id)

    @pytest.mark.parametrize(
        ('view', 'count_key', 'roles', 'expected_status'),
        [
            (PoolCancelQueuedView, 'cancelled_count', ['ROLE_SANDBOX-SERVICE_ORGANIZER'], 200),
            (PoolCancelQueuedView, 'cancelled_count', ['ROLE_SANDBOX-SERVICE_ADMIN'], 200),
            (PoolCancelQueuedView, 'cancelled_count', ['ROLE_SANDBOX-SERVICE_TRAINEE'], 403),
            (
                PoolForceCancelAllocationView,
                'force_cancelled_count',
                ['ROLE_SANDBOX-SERVICE_ADMIN'],
                200,
            ),
            (
                PoolForceCancelAllocationView,
                'force_cancelled_count',
                ['ROLE_SANDBOX-SERVICE_ORGANIZER'],
                403,
            ),
            (PoolForceCleanupView, 'force_cleaned_count', ['ROLE_SANDBOX-SERVICE_ADMIN'], 200),
            (PoolForceCleanupView, 'force_cleaned_count', ['ROLE_SANDBOX-SERVICE_ORGANIZER'], 403),
        ],
    )
    def test_roles_and_answer(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, mocker, pool, view, count_key, roles, expected_status
    ):
        """Cancelling queued work is for organizers; removing started work is admin-only."""
        make_unit(pool)

        response = self.post(mocker, view, roles, pool.id)

        assert response.status_code == expected_status
        if expected_status == 200:
            assert set(response.data) == {count_key}

    def test_cancel_queued_answers_the_count(self, mocker, pool):
        """The answer counts the removed units."""
        make_unit(pool)
        make_unit(pool, 'queued')

        response = self.post(mocker, PoolCancelQueuedView, ['ROLE_SANDBOX-SERVICE_ADMIN'], pool.id)

        assert response.data == {'cancelled_count': 2}

    def test_unknown_pool_is_not_found(self, mocker):
        """A pool that does not exist answers 404."""
        response = self.post(mocker, PoolForceCleanupView, ['ROLE_SANDBOX-SERVICE_ADMIN'], 999)

        assert response.status_code == status.HTTP_404_NOT_FOUND
