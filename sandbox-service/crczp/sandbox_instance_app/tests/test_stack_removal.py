"""Tests for the removal of every leftover of one stack."""

from types import SimpleNamespace

import pytest

from crczp.cloud_commons import CloudResource, DeletionOutcome, ResourceDeletion, StackNameScheme
from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_instance_app.lib import stack_removal
from crczp.sandbox_instance_app.lib.netbird_orphans import (
    NetbirdDeletionResult,
    NetbirdObject,
    NetbirdStackObjects,
)
from crczp.terraform_driver.terraform_exceptions import TerraformStateLocked

STACK_NAME = 'test-p0000000001-s0000900001'


@pytest.fixture(name='terraform')
def fixture_terraform(mocker):
    """A terraform client whose destroy succeeds and whose deletion by name finds nothing."""
    client = mocker.MagicMock()
    client.delete_stack.return_value = mocker.MagicMock()
    client.wait_for_destroy.return_value = ('', '', 0)
    client.delete_stack_resources.return_value = []
    mocker.patch.object(stack_removal.utils, 'get_terraform_client', return_value=client)
    return client


@pytest.fixture(name='netbird')
def fixture_netbird(mocker):
    """A configured NetBird client; the listing finds nothing and deletions succeed."""
    client = mocker.MagicMock()
    mocker.patch.object(stack_removal, 'get_netbird_client', return_value=client)
    listing = mocker.patch.object(
        stack_removal.netbird_orphans, 'list_netbird_objects_by_stack', return_value={}
    )
    deletion = mocker.patch.object(
        stack_removal.netbird_orphans, 'delete_netbird_stack_objects', return_value=[]
    )
    return SimpleNamespace(client=client, listing=listing, deletion=deletion)


@pytest.fixture(name='jump_host')
def fixture_jump_host(mocker):
    """A jump host whose home removal succeeds."""
    removal = mocker.patch.object(stack_removal.jump_proxy_cleanup, 'remove_jump_home')
    return SimpleNamespace(removal=removal)


@pytest.fixture(name='sources', autouse=True)
def fixture_sources(terraform, netbird, jump_host):
    """Every source of leftovers, mocked."""
    return SimpleNamespace(terraform=terraform, netbird=netbird, jump_host=jump_host)


def test_destroys_then_removes_netbird_objects_and_jump_home(sources, mocker):
    """A destroy is followed by the deletion by name, the forced workspace deletion, NetBird
    and the jump home."""
    objects = NetbirdStackObjects(
        groups=[NetbirdObject(kind='group', object_id='g1', name=STACK_NAME)]
    )
    sources.netbird.listing.return_value = {STACK_NAME: objects}
    order = mocker.MagicMock()
    order.attach_mock(sources.terraform.delete_stack, 'destroy')
    order.attach_mock(sources.terraform.delete_terraform_workspace, 'delete_workspace')
    order.attach_mock(sources.terraform.delete_stack_directory, 'delete_directory')
    order.attach_mock(sources.terraform.delete_stack_resources, 'delete_by_name')
    order.attach_mock(sources.netbird.deletion, 'delete_netbird')
    order.attach_mock(sources.jump_host.removal, 'remove_jump_home')

    assert stack_removal.remove_stack(STACK_NAME) == ''

    assert [call[0] for call in order.mock_calls] == [
        'destroy',
        'delete_by_name',
        'delete_workspace',
        'delete_directory',
        'delete_netbird',
        'remove_jump_home',
    ]
    sources.terraform.delete_terraform_workspace.assert_called_once_with(STACK_NAME, force=True)
    sources.netbird.deletion.assert_called_once_with(sources.netbird.client, objects)
    assert sources.jump_host.removal.call_args.args[0] == STACK_NAME


def test_releases_a_held_state_lock_and_destroys_again(sources):
    """A destroy blocked by a state lock force-unlocks it and runs once more."""
    sources.terraform.wait_for_destroy.side_effect = [TerraformStateLocked('lock-1'), ('', '', 0)]

    assert stack_removal.remove_stack(STACK_NAME) == ''

    sources.terraform.force_unlock_stack.assert_called_once_with(STACK_NAME, 'lock-1')
    assert sources.terraform.delete_stack.call_count == 2


def test_force_deletes_the_workspace_after_a_failed_destroy_and_clean_sweep(sources):
    """A failed destroy followed by a sweep that leaves nothing still frees the workspace."""
    sources.terraform.wait_for_destroy.return_value = ('', '', 1)

    assert stack_removal.remove_stack(STACK_NAME) == ''

    sources.terraform.delete_terraform_workspace.assert_called_once_with(STACK_NAME, force=True)


@pytest.mark.parametrize(
    ('has_state', 'destroy_result'),
    [(False, None), (True, ('', '', 1))],
    ids=['no state', 'destroy failed'],
)
def test_deletes_by_name_without_state_or_after_a_failed_destroy(
    sources, mocker, has_state, destroy_result
):
    """Deletion by name takes over and the directory is removed."""
    sources.terraform.delete_stack.return_value = mocker.MagicMock() if has_state else None
    sources.terraform.wait_for_destroy.return_value = destroy_result
    server = CloudResource(kind='server', resource_id='s1', name='node')
    sources.terraform.delete_stack_resources.return_value = [
        ResourceDeletion(resource=server, outcome=DeletionOutcome.DELETED)
    ]

    assert stack_removal.remove_stack(STACK_NAME) == ''

    sources.terraform.delete_stack_resources.assert_called_once()
    assert sources.terraform.delete_stack_resources.call_args.args[0] == STACK_NAME
    sources.terraform.delete_terraform_workspace.assert_called_once_with(STACK_NAME, force=True)
    sources.terraform.delete_stack_directory.assert_called_once_with(STACK_NAME)


def test_a_resource_left_after_a_successful_destroy_is_reported(sources):
    """A resource the deletion by name cannot remove is reported; the workspace and state stay."""
    server = CloudResource(kind='server', resource_id='s1', name='node')
    sources.terraform.delete_stack_resources.return_value = [
        ResourceDeletion(resource=server, outcome=DeletionOutcome.FAILED, error='busy')
    ]

    assert stack_removal.remove_stack(STACK_NAME) == 'Delete by name failed: server node: busy.'

    sources.terraform.delete_terraform_workspace.assert_not_called()
    sources.terraform.delete_stack_directory.assert_not_called()


def test_every_failure_is_reported_and_no_step_blocks_the_next(sources):
    """Failed deletions by name and NetBird objects are reported; the jump home still goes."""
    sources.terraform.delete_stack.return_value = None
    server = CloudResource(kind='server', resource_id='s1', name='node')
    sources.terraform.delete_stack_resources.return_value = [
        ResourceDeletion(resource=server, outcome=DeletionOutcome.FAILED, error='busy')
    ]
    sources.netbird.listing.return_value = {STACK_NAME: NetbirdStackObjects()}
    sources.netbird.deletion.return_value = [
        NetbirdDeletionResult(kind='group', object_id='g1', name='group', error='HTTP 500')
    ]
    sources.jump_host.removal.side_effect = exceptions.NetworkError('ssh refused')

    assert stack_removal.remove_stack(STACK_NAME) == (
        'No OpenTofu state. Delete by name failed: server node: busy. '
        'NetBird deletion failed: group group: HTTP 500. ssh refused'
    )


def test_delete_resources_by_name_reports_only_the_resources_left(terraform):
    """Resources that are gone are not reported; each one left carries its error."""
    gone = CloudResource(kind='server', resource_id='s1', name='node')
    stuck = CloudResource(kind='network', resource_id='n1', name='net')
    terraform.delete_stack_resources.return_value = [
        ResourceDeletion(resource=gone, outcome=DeletionOutcome.DELETED),
        ResourceDeletion(resource=stuck, outcome=DeletionOutcome.FAILED, error='busy'),
    ]
    stack_names = StackNameScheme('test')

    failures = stack_removal.delete_resources_by_name(terraform, STACK_NAME, stack_names)

    assert failures == ['network net: busy']
    terraform.delete_stack_resources.assert_called_once_with(STACK_NAME, stack_names)
