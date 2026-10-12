"""Tests for the periodic cleanup of the sandboxes trainees allocated for themselves."""

import copy
from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from crczp.sandbox_instance_app.lib import requests
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    CleanupRequest,
    Pool,
    Sandbox,
    SandboxAllocationUnit,
    StackAllocationStage,
    StackCleanupStage,
)
from crczp.sandbox_instance_app.tests.conftest import (
    set_stage_failed,
    set_stage_finished,
    set_stage_started,
)

pytestmark = pytest.mark.django_db

NOW = timezone.now()
CUTOFF = NOW - timedelta(hours=24)
OLD = NOW - timedelta(hours=30)
YOUNG = NOW - timedelta(hours=2)


def make_unit(
    pool: Pool, created_at: Any, sub: str | None = 'trainee-sub', stack: str = 'done'
) -> Any:
    """Create a unit with its sandbox; its stack stage is 'done' or 'running'."""
    unit = SandboxAllocationUnit.objects.create(
        pool=pool, created_by_sub=sub, created_at=created_at
    )
    request = AllocationRequest.objects.create(allocation_unit=unit)
    stage = StackAllocationStage.objects.create(
        allocation_request=request, allocation_request_fk_many=request
    )
    (set_stage_finished if stack == 'done' else set_stage_started)(stage)
    Sandbox.objects.create(
        id=f'sandbox-{unit.id}',
        allocation_unit=unit,
        private_user_key='private-key',
        public_user_key='public-key',
        ready=stack == 'done',
    )
    return unit


def add_cleanup(unit: Any, state: str) -> None:
    """Give the unit a cleanup request whose stack stage is 'running' or 'failed'."""
    request = CleanupRequest.objects.create(allocation_unit=unit)
    stage = StackCleanupStage.objects.create(
        cleanup_request=request, cleanup_request_fk_many=request
    )
    (set_stage_started if state == 'running' else set_stage_failed)(stage)


@pytest.fixture(name='cleanup_handler')
def fixture_cleanup_handler(mocker):
    """The cleanup request handler, which enqueues the cleanup stages."""
    mocker.patch('crczp.sandbox_instance_app.lib.requests.netbird.destroy_netbird_for_sandbox')
    return mocker.patch(
        'crczp.sandbox_instance_app.lib.requests.request_handlers.CleanupRequestHandler'
    )


class TestCleanupExpiredTraineeUnits:
    """requests.cleanup_expired_trainee_units."""

    def test_cleans_up_old_trainee_sandboxes_only(self, pool, cleanup_handler):
        """Old trainee units go; young ones and organizers' units stay."""
        old = make_unit(pool, OLD)
        make_unit(pool, YOUNG)
        make_unit(pool, OLD, sub=None)

        report = requests.cleanup_expired_trainee_units(CUTOFF)

        assert report.cleaned == [old.id]
        assert not Sandbox.objects.filter(allocation_unit=old).exists()
        cleanup_handler.return_value.enqueue_request.assert_called_once_with(old)

    def test_running_cleanup_is_left_alone(self, pool, cleanup_handler):
        """A unit already being cleaned up is not touched."""
        cleaning = make_unit(pool, OLD)
        add_cleanup(cleaning, 'running')

        report = requests.cleanup_expired_trainee_units(CUTOFF)

        assert report == requests.TraineeCleanupReport()
        cleanup_handler.return_value.enqueue_request.assert_not_called()

    def test_failed_cleanup_is_retried(self, pool, cleanup_handler):
        """A unit whose cleanup failed is cleaned up again."""
        failed = make_unit(pool, OLD)
        add_cleanup(failed, 'failed')

        report = requests.cleanup_expired_trainee_units(CUTOFF)

        assert report.retried == [failed.id]
        cleanup_handler.return_value.enqueue_request.assert_called_once_with(failed)

    def test_unit_whose_first_stage_runs_is_skipped(self, pool, cleanup_handler):
        """A unit that cannot be cleaned up yet waits for the next run."""
        running = make_unit(pool, OLD, stack='running')

        report = requests.cleanup_expired_trainee_units(CUTOFF)

        assert report.skipped == [running.id]
        cleanup_handler.return_value.enqueue_request.assert_not_called()

    def test_one_failing_unit_does_not_stop_the_run(self, pool, cleanup_handler):
        """A unit whose cleanup cannot be enqueued is reported; the others are cleaned up."""
        first, second = make_unit(pool, OLD), make_unit(pool, OLD)
        cleanup_handler.return_value.enqueue_request.side_effect = [ConnectionError(), None]

        report = requests.cleanup_expired_trainee_units(CUTOFF)

        assert report.failed == [first.id]
        assert report.cleaned == [second.id]

    def test_dry_run_changes_nothing(self, pool, cleanup_handler):
        """A dry run only reports what would be cleaned up."""
        old = make_unit(pool, OLD)

        report = requests.cleanup_expired_trainee_units(CUTOFF, dry_run=True)

        assert report.cleaned == [old.id]
        assert Sandbox.objects.filter(allocation_unit=old).exists()
        cleanup_handler.assert_not_called()


class TestCleanupTraineeSandboxesCommand:
    """The cleanup_trainee_sandboxes management command."""

    @pytest.fixture
    def configure(self, mocker):
        """Return a function that sets trainee_sandbox_cleanup on a copy of the app config."""

        def set_cleanup(*, enabled: bool, max_age_hours: int = 24) -> None:
            config = copy.copy(settings.CRCZP_CONFIG)
            cleanup = copy.copy(config.trainee_sandbox_cleanup)
            cleanup.enabled = enabled
            cleanup.max_age_hours = max_age_hours
            config.trainee_sandbox_cleanup = cleanup
            mocker.patch.object(settings, 'CRCZP_CONFIG', config)

        return set_cleanup

    @pytest.fixture
    def cleanup(self, mocker):
        """The cleanup itself."""
        return mocker.patch(
            'crczp.sandbox_instance_app.lib.requests.cleanup_expired_trainee_units',
            return_value=requests.TraineeCleanupReport(cleaned=[1], skipped=[2]),
        )

    def test_does_nothing_while_disabled(self, configure, cleanup):
        """The cleanup is off unless the deployment enables it."""
        configure(enabled=False)
        out = StringIO()

        call_command('cleanup_trainee_sandboxes', stdout=out)

        cleanup.assert_not_called()
        assert 'disabled' in out.getvalue()

    @pytest.mark.parametrize('dry_run', [False, True])
    def test_cleans_up_sandboxes_older_than_the_configured_age(self, configure, cleanup, dry_run):
        """Sandboxes older than max_age_hours are cleaned up, or only listed in a dry run."""
        configure(enabled=True, max_age_hours=6)
        out = StringIO()

        call_command('cleanup_trainee_sandboxes', *(['--dry-run'] if dry_run else []), stdout=out)

        older_than = cleanup.call_args.args[0]
        assert timezone.now() - timedelta(hours=6, minutes=1) < older_than
        assert older_than < timezone.now() - timedelta(hours=5, minutes=59)
        assert cleanup.call_args.kwargs == {'dry_run': dry_run}
        assert ('Would clean up 1' if dry_run else 'Cleaned up 1') in out.getvalue()

    def test_fails_when_a_unit_could_not_be_cleaned_up(self, configure, cleanup):
        """A failed unit fails the run, so the scheduler shows it."""
        configure(enabled=True)
        cleanup.return_value = requests.TraineeCleanupReport(cleaned=[1], failed=[3])

        with pytest.raises(CommandError, match=r'\[3\]'):
            call_command('cleanup_trainee_sandboxes', stdout=StringIO())
