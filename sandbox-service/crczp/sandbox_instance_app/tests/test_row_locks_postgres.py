"""
Row-lock behaviour of the cancellation that SQLite cannot show.

SQLite ignores ``FOR UPDATE``, so these tests run only against PostgreSQL: point the Django
test settings at a PostgreSQL database to run them. Each test commits for real and uses
threads with their own connections.
"""

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.core.management import call_command
from django.db import close_old_connections, connection, transaction
from django.test.utils import CaptureQueriesContext

from crczp.sandbox_instance_app.lib import allocation_cancellation, stage_handlers
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    Pool,
    SandboxAllocationUnit,
    StackAllocationStage,
)
from crczp.sandbox_instance_app.tests.conftest import TESTING_DATABASE, data_path_join

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != 'postgresql', reason='needs PostgreSQL row locks'),
]

THREAD_TIMEOUT_SECONDS = 20


@pytest.fixture(autouse=True)
def restore_fixture_data() -> Iterator[None]:
    """A transactional test flushes the database; load the session fixture data again."""
    yield
    call_command('loaddata', data_path_join(TESTING_DATABASE))


def make_queued_unit(pool: Pool) -> SandboxAllocationUnit:
    """Create a unit with its allocation request and no stages, and count it in the pool."""
    unit = SandboxAllocationUnit.objects.create(pool=pool)
    AllocationRequest.objects.create(allocation_unit=unit)
    Pool.objects.filter(pk=pool.pk).update(size=pool.size + 1)
    pool.refresh_from_db()
    return unit


def run_in_thread(target: Callable[[], Any], results: list[Any]) -> threading.Thread:
    """Run the target on its own database connection and keep its result or its error."""

    def run() -> None:
        try:
            results.append(target())
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            results.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def wait_until_blocked_on_a_lock() -> None:
    """Wait until a backend of the database waits for a lock held by another one."""
    for _ in range(THREAD_TIMEOUT_SECONDS * 20):
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'")
            if cursor.fetchone()[0]:
                return
        time.sleep(0.05)
    pytest.fail('No backend waited for a lock.')


def test_removing_a_queued_unit_locks_without_an_outer_join_error(pool):
    """Locking the units of a queued allocation must not hit PostgreSQL's outer join refusal."""
    unit = make_queued_unit(pool)

    removed_ids = allocation_cancellation._remove_stageless_units(  # pylint: disable=protected-access
        {unit.id: unit}
    )

    assert removed_ids == {unit.id}
    assert not SandboxAllocationUnit.objects.filter(pk=unit.pk).exists()
    pool.refresh_from_db()
    assert pool.size == 0


def test_unit_whose_creation_job_commits_stages_meanwhile_is_not_removed(pool):
    """
    The creation job holds the unit's row lock while it creates the stages. A removal waiting
    for that lock must see the committed stages and leave the unit alone.
    """
    unit = make_queued_unit(pool)
    lock_held = threading.Event()
    stages_may_commit = threading.Event()
    creation_results: list[Any] = []
    removal_results: list[Any] = []

    def create_stages_under_the_unit_lock() -> None:
        with transaction.atomic():
            SandboxAllocationUnit.objects.select_for_update().get(pk=unit.pk)
            lock_held.set()
            assert stages_may_commit.wait(THREAD_TIMEOUT_SECONDS)
            allocation_request = AllocationRequest.objects.get(allocation_unit=unit)
            StackAllocationStage.objects.create(
                allocation_request=allocation_request, allocation_request_fk_many=allocation_request
            )

    creation = run_in_thread(create_stages_under_the_unit_lock, creation_results)
    assert lock_held.wait(THREAD_TIMEOUT_SECONDS)
    removal = run_in_thread(
        lambda: allocation_cancellation._remove_stageless_units({unit.id: unit}),  # pylint: disable=protected-access
        removal_results,
    )
    wait_until_blocked_on_a_lock()
    stages_may_commit.set()
    creation.join(THREAD_TIMEOUT_SECONDS)
    removal.join(THREAD_TIMEOUT_SECONDS)
    close_old_connections()

    assert creation_results == [None]
    assert removal_results == [set()]
    assert SandboxAllocationUnit.objects.filter(pk=unit.pk).exists()
    assert StackAllocationStage.objects.filter(allocation_request__allocation_unit=unit).exists()
    pool.refresh_from_db()
    assert pool.size == 1


def test_the_failure_mark_is_one_guarded_statement_on_the_stage_table(pool):
    """
    A stage finished by its worker between a pre-select and the update must stay as it was:
    the guards live in the UPDATE itself, so no later statement can overwrite the finished row.
    """
    unit = make_queued_unit(pool)
    allocation_request = AllocationRequest.objects.get(allocation_unit=unit)
    stage = StackAllocationStage.objects.create(
        allocation_request=allocation_request, allocation_request_fk_many=allocation_request
    )
    stage.finished = True
    stage.save()
    stale_stage_view = StackAllocationStage.objects.get(pk=stage.pk)
    stale_stage_view.finished = False
    handler = stage_handlers.AllocationStackStageHandler(stale_stage_view)

    with CaptureQueriesContext(connection) as captured:
        marked = handler.mark_abandoned()

    assert marked is False
    unfinished_guard = 'NOT "sandbox_instance_app_allocationstage"."finished"'
    statements = [query['sql'] for query in captured.captured_queries]
    assert not any(sql.startswith('SELECT') and unfinished_guard in sql for sql in statements)
    assert any(sql.startswith('UPDATE') and unfinished_guard in sql for sql in statements)
    stage.refresh_from_db()
    assert stage.failed is False
