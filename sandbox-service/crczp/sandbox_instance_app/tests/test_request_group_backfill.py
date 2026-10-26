"""Tests for the data migration that assigns in-flight allocation requests to their groups."""

import importlib

import pytest
from django.apps import apps

from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    Pool,
    SandboxAllocationUnit,
    SandboxRequestGroup,
    StackAllocationStage,
)

pytestmark = pytest.mark.django_db

backfill = importlib.import_module(
    'crczp.sandbox_instance_app.migrations.0027_request_group_backfill'
)


def make_request(pool: Pool, *, finished: bool) -> AllocationRequest:
    unit = SandboxAllocationUnit.objects.create(pool=pool)
    allocation_request = AllocationRequest.objects.create(allocation_unit=unit)
    StackAllocationStage.objects.create(
        allocation_request=allocation_request,
        allocation_request_fk_many=allocation_request,
        finished=finished,
    )
    return allocation_request


def group_of(allocation_request: AllocationRequest) -> SandboxRequestGroup | None:
    allocation_request.refresh_from_db()
    return allocation_request.request_group


def run_backfill() -> None:
    backfill.assign_in_flight_requests_to_groups(apps, None)


def test_in_flight_requests_fill_the_open_places_of_their_group_oldest_first(pool):
    group = SandboxRequestGroup.objects.create(
        pool=pool, unit_count=3, finished_count=1, email='a@b.c'
    )
    first, second, third = (make_request(pool, finished=False) for _ in range(3))

    run_backfill()

    assert [group_of(first), group_of(second), group_of(third)] == [group, group, None]


def test_a_finished_request_is_left_out_of_the_group(pool):
    group = SandboxRequestGroup.objects.create(pool=pool, unit_count=2, email='a@b.c')
    finished = make_request(pool, finished=True)
    building = make_request(pool, finished=False)

    run_backfill()

    assert (group_of(finished), group_of(building)) == (None, group)


def test_a_group_with_every_unit_ended_takes_no_request(pool):
    SandboxRequestGroup.objects.create(pool=pool, unit_count=1, finished_count=1, email='a@b.c')
    building = make_request(pool, finished=False)

    run_backfill()

    assert group_of(building) is None


def test_requests_of_another_pool_are_not_assigned(pool, definition, created_by):
    SandboxRequestGroup.objects.create(pool=pool, unit_count=2, email='a@b.c')
    other_pool = Pool.objects.create(
        definition=definition,
        max_size=3,
        private_management_key='-----RSA PRIVATE KEY-----',
        public_management_key='ssh-rsa',
        uuid='1fb3160d',
        created_by=created_by,
    )
    foreign = make_request(other_pool, finished=False)

    run_backfill()

    assert group_of(foreign) is None
