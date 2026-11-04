"""Removal of every leftover of one stack: cloud resources, NetBird objects and jump-host home."""

from collections.abc import Callable

import structlog
from django.conf import settings

from crczp.cloud_commons import CrczpException, StackNameScheme
from crczp.sandbox_common_lib import utils
from crczp.sandbox_common_lib.netbird_client import get_netbird_client
from crczp.sandbox_instance_app.lib import jump_proxy_cleanup, netbird_orphans
from crczp.terraform_driver.terraform_client import CrczpTerraformClient
from crczp.terraform_driver.terraform_exceptions import TerraformStateLocked

LOG = structlog.get_logger()


def remove_stack(stack_name: str) -> str:
    """
    Remove the leftovers of one stack that no allocation unit owns.

    The stack is destroyed through its OpenTofu state, with a stale state lock released, and
    every cloud resource left is deleted by name; then its NetBird objects and its jump-host
    home are removed. Each removal runs even when an earlier one failed. The caller has to
    confirm that no unit of the stack exists, so that no job can still hold its state lock.

    :param stack_name: The full name of the stack.
    :returns: '' once every removal succeeded, else why the failed ones failed.
    """
    stack_names = StackNameScheme(settings.CRCZP_SERVICE_CONFIG.stack_name_prefix)
    errors = [
        error
        for removal in (_remove_cloud_stack, _remove_netbird_objects, _remove_jump_home)
        if (error := _run_removal(removal, stack_name, stack_names))
    ]
    return ' '.join(errors)


def _run_removal(
    removal: Callable[[str, StackNameScheme], str], stack_name: str, stack_names: StackNameScheme
) -> str:
    """Run one removal of an orphan; returns why it failed, or '' once it succeeded."""
    try:
        return removal(stack_name, stack_names)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOG.exception(
            'stack_removal_step_failed',
            stack_name=stack_name,
            removal=getattr(removal, '__name__', repr(removal)),
        )
        return _describe(exc)


def _remove_cloud_stack(stack_name: str, stack_names: StackNameScheme) -> str:
    """
    Destroy the stack through its state, then delete by name every cloud resource of the
    stack that is left. Once none is left, force-delete the stack's workspace and the local
    stack directory; while a resource is left, both stay so that the state still tracks it.

    :returns: '' once no resource is left, else the destroy error and the deletions that failed.
    """
    client = utils.get_terraform_client()
    destroy_error = _destroy_with_lock_release(client, stack_name)
    failures = delete_resources_by_name(client, stack_name, stack_names)
    if not failures:
        delete_workspace(client, stack_name)
        delete_stack_directory(client, stack_name)
        return ''
    deletion_error = f'Delete by name failed: {"; ".join(failures)}.'
    return f'{destroy_error} {deletion_error}' if destroy_error else deletion_error


def delete_resources_by_name(
    client: CrczpTerraformClient, stack_name: str, stack_names: StackNameScheme
) -> list[str]:
    """
    Delete the cloud resources of one stack, dependents first, through the cloud API.

    Only a resource whose name is canonical for the stack, or that carries its stack-name tag,
    is deleted; no name is matched by prefix.

    :returns: One description per resource that is still there; empty once none is left.
    """
    return [
        f'{deletion.resource.kind} {deletion.resource.name or deletion.resource.resource_id}: '
        f'{deletion.error}'
        for deletion in client.delete_stack_resources(stack_name, stack_names)
        if not deletion.gone
    ]


def _destroy_with_lock_release(client: CrczpTerraformClient, stack_name: str) -> str | None:
    """
    Destroy the stack through its OpenTofu state, once more after releasing a held state lock.

    The caller has confirmed that no unit of the stack exists, so no job can still hold the lock.

    :returns: Why the destroy did not remove the stack, or None once it did.
    """
    try:
        try:
            return_code = _run_destroy(client, stack_name)
        except TerraformStateLocked as locked:
            LOG.warning('stack_removal_releasing_state_lock', stack_name=stack_name)
            client.force_unlock_stack(stack_name, locked.lock_id)
            return_code = _run_destroy(client, stack_name)
    except (CrczpException, ValueError) as exc:
        return f'Destroy failed: {_describe(exc)}.'
    if return_code is None:
        return 'No OpenTofu state.'
    if return_code:
        return f'Destroy failed with exit status {return_code}.'
    return None


def _run_destroy(client: CrczpTerraformClient, stack_name: str) -> int | None:
    """Run the destroy; returns its exit status, or None when the stack has no state."""
    process = client.delete_stack(stack_name)
    if process is None:
        return None
    _stdout, _stderr, return_code = client.wait_for_destroy(
        process, settings.CRCZP_CONFIG.sandbox_build_timeout
    )
    return return_code


def delete_workspace(client: CrczpTerraformClient, stack_name: str, force: bool = True) -> None:
    """
    Delete the stack's OpenTofu workspace, logging a failure.

    :param force: Delete the workspace even when its state still tracks resources.
    """
    try:
        client.delete_terraform_workspace(stack_name, force=force)
    except CrczpException as exc:
        LOG.warning('stack_removal_workspace_delete_failed', stack_name=stack_name, error=str(exc))


def delete_stack_directory(client: CrczpTerraformClient, stack_name: str) -> None:
    """Delete the stack's local OpenTofu directory, logging a failure."""
    try:
        client.delete_stack_directory(stack_name)
    except (CrczpException, OSError) as exc:
        LOG.warning(
            'stack_removal_stack_directory_delete_failed', stack_name=stack_name, error=str(exc)
        )


def _remove_netbird_objects(stack_name: str, stack_names: StackNameScheme) -> str:
    client = get_netbird_client()
    if client is None:
        return ''
    objects = netbird_orphans.list_netbird_objects_by_stack(client, stack_names).get(stack_name)
    if objects is None:
        return ''
    failures = [
        f'{result.kind} {result.name}: {result.error}'
        for result in netbird_orphans.delete_netbird_stack_objects(client, objects)
        if result.error
    ]
    return f'NetBird deletion failed: {"; ".join(failures)}.' if failures else ''


def _remove_jump_home(stack_name: str, stack_names: StackNameScheme) -> str:
    jump_proxy_cleanup.remove_jump_home(stack_name, stack_names)
    return ''


def _describe(error: Exception) -> str:
    return str(error) or type(error).__name__
