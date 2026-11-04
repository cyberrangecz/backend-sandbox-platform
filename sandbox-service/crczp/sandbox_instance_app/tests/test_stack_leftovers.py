"""Tests of the removal of stacks that no allocation unit owns."""

# pylint: disable=missing-function-docstring,redefined-outer-name
import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from crczp.cloud_commons import StackNameScheme
from crczp.sandbox_instance_app.lib import stack_leftovers
from crczp.sandbox_instance_app.models import SandboxAllocationUnit

pytestmark = pytest.mark.django_db

STACK_NAMES = StackNameScheme(settings.CRCZP_SERVICE_CONFIG.stack_name_prefix)


def stack_name(pool_id: int, unit_id: int) -> str:
    return f'{STACK_NAMES.prefix}-p{pool_id:010d}-s{unit_id:010d}'


ORPHAN = stack_name(900, 901)
OTHER_ORPHAN = stack_name(900, 902)


@pytest.fixture
def listings(mocker):
    """The names each place lists; a value of None makes that listing fail."""
    names: dict[str, list[str] | None] = {
        'cloud': [],
        'workspaces': [],
        'netbird': [],
        'jump': [],
    }

    def listed(key: str) -> list[str]:
        value = names[key]
        if value is None:
            raise ConnectionError(f'{key} unreachable')
        return value

    client = mocker.Mock()
    client.list_stack_inventories.side_effect = lambda _: {n: None for n in listed('cloud')}
    client.list_workspaces.side_effect = lambda _: listed('workspaces')
    mocker.patch.object(stack_leftovers.utils, 'get_terraform_client', return_value=client)
    mocker.patch.object(stack_leftovers, 'get_netbird_client', return_value=mocker.Mock())
    mocker.patch.object(
        stack_leftovers.netbird_orphans,
        'list_netbird_objects_by_stack',
        side_effect=lambda *_: {n: None for n in listed('netbird')},
    )
    mocker.patch.object(
        stack_leftovers.jump_proxy_cleanup,
        'list_jump_homes',
        side_effect=lambda _: listed('jump'),
    )
    return names


@pytest.fixture
def remove_stack(mocker):
    return mocker.patch.object(stack_leftovers, 'remove_stack', return_value='')


class TestRemoveLeftoverStacks:
    def test_a_stack_found_in_several_places_is_removed_once(self, listings, remove_stack):
        listings['cloud'] = [ORPHAN]
        listings['workspaces'] = [ORPHAN]
        listings['jump'] = [ORPHAN]

        report = stack_leftovers.remove_leftover_stacks()

        remove_stack.assert_called_once_with(ORPHAN)
        assert report.removed == {
            ORPHAN: [
                stack_leftovers.CLOUD_SOURCE,
                stack_leftovers.WORKSPACE_SOURCE,
                stack_leftovers.JUMP_HOME_SOURCE,
            ]
        }

    def test_the_stacks_of_every_place_are_united(self, listings, remove_stack):
        listings['cloud'] = [ORPHAN]
        listings['netbird'] = [OTHER_ORPHAN]

        stack_leftovers.remove_leftover_stacks()

        assert sorted(call.args[0] for call in remove_stack.call_args_list) == [
            ORPHAN,
            OTHER_ORPHAN,
        ]

    def test_a_stack_owned_by_a_unit_is_left_alone(self, listings, remove_stack, allocation_unit):
        owned = allocation_unit.get_stack_name()
        listings['cloud'] = [owned]

        report = stack_leftovers.remove_leftover_stacks()

        remove_stack.assert_not_called()
        assert report.skipped == [owned]

    def test_a_stack_whose_pool_differs_from_the_unit_pool_is_not_owned(
        self, listings, remove_stack, allocation_unit
    ):
        listings['cloud'] = [stack_name(allocation_unit.pool_id + 1, allocation_unit.pk)]

        stack_leftovers.remove_leftover_stacks()

        remove_stack.assert_called_once()

    def test_the_owner_is_checked_again_right_before_each_removal(
        self, listings, mocker, allocation_unit
    ):
        listings['cloud'] = [allocation_unit.get_stack_name()]
        listings['workspaces'] = [ORPHAN]
        removed = mocker.patch.object(stack_leftovers, 'remove_stack', return_value='')
        mocker.patch.object(stack_leftovers, '_is_owned', side_effect=[False, True])

        stack_leftovers.remove_leftover_stacks()

        assert removed.call_count == 1

    def test_dry_run_removes_nothing_and_reports_the_stacks(self, listings, remove_stack):
        listings['cloud'] = [ORPHAN]

        report = stack_leftovers.remove_leftover_stacks(dry_run=True)

        remove_stack.assert_not_called()
        assert list(report.removed) == [ORPHAN]

    def test_a_failing_listing_does_not_stop_the_others(self, listings, remove_stack):
        listings['cloud'] = None
        listings['workspaces'] = [ORPHAN]

        report = stack_leftovers.remove_leftover_stacks()

        remove_stack.assert_called_once_with(ORPHAN)
        assert list(report.failed) == [stack_leftovers.CLOUD_SOURCE]

    def test_a_failing_removal_is_reported_and_the_others_run(self, listings, mocker):
        listings['cloud'] = [ORPHAN, OTHER_ORPHAN]
        mocker.patch.object(stack_leftovers, 'remove_stack', side_effect=['stuck', ''])

        report = stack_leftovers.remove_leftover_stacks()

        assert report.failed == {ORPHAN: 'stuck'}
        assert list(report.removed) == [OTHER_ORPHAN]

    def test_without_a_netbird_client_that_place_is_skipped(self, listings, mocker, remove_stack):
        mocker.patch.object(stack_leftovers, 'get_netbird_client', return_value=None)
        listings['netbird'] = [ORPHAN]

        report = stack_leftovers.remove_leftover_stacks()

        remove_stack.assert_not_called()
        assert not report.removed


class TestRemoveLeftoverStacksCommand:
    def test_it_removes_the_stacks_no_unit_owns(self, listings, remove_stack):
        listings['cloud'] = [ORPHAN]

        call_command('remove_leftover_stacks')

        remove_stack.assert_called_once_with(ORPHAN)

    def test_dry_run_removes_nothing(self, listings, remove_stack):
        listings['cloud'] = [ORPHAN]

        call_command('remove_leftover_stacks', '--dry-run')

        remove_stack.assert_not_called()

    def test_it_exits_non_zero_when_a_removal_failed(self, listings, mocker):
        listings['cloud'] = [ORPHAN]
        mocker.patch.object(stack_leftovers, 'remove_stack', return_value='stuck')

        with pytest.raises(CommandError):
            call_command('remove_leftover_stacks')

    def test_the_units_are_never_touched(self, listings, remove_stack, allocation_unit):
        listings['cloud'] = [ORPHAN]

        call_command('remove_leftover_stacks')

        assert SandboxAllocationUnit.objects.filter(pk=allocation_unit.pk).exists()
