"""Removal of the stacks that no allocation unit owns, found wherever a stack leaves traces."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

import structlog
from django.conf import settings

from crczp.cloud_commons import StackNameScheme
from crczp.sandbox_common_lib import utils
from crczp.sandbox_common_lib.netbird_client import get_netbird_client
from crczp.sandbox_instance_app.lib import jump_proxy_cleanup, netbird_orphans
from crczp.sandbox_instance_app.lib.stack_removal import remove_stack
from crczp.sandbox_instance_app.models import SandboxAllocationUnit

LOG = structlog.get_logger()

CLOUD_SOURCE = 'cloud resources'
WORKSPACE_SOURCE = 'terraform workspaces'
NETBIRD_SOURCE = 'netbird objects'
JUMP_HOME_SOURCE = 'jump host homes'


@dataclass(frozen=True)
class LeftoverReport:
    """
    The outcome of one leftover sweep.

    :param removed: The stacks removed, or found when the sweep is a dry run.
    :param skipped: The stacks a unit owns, which are left alone.
    :param failed: Why the removal of each stack failed, or why a listing failed.
    """

    removed: dict[str, list[str]]
    skipped: list[str]
    failed: dict[str, str]


def remove_leftover_stacks(*, dry_run: bool = False) -> LeftoverReport:
    """
    Remove every stack that no allocation unit owns, the way the cleanup removes a stack.

    The stacks are the union of those the cloud project, the terraform backend, NetBird and
    the jump host list under the deployment's stack names. A listing that fails is reported
    and the others still run. The owner is checked again right before each removal.

    :param dry_run: Only report the stacks that would be removed.
    :return: What was removed, skipped and what failed.
    """
    stack_names = StackNameScheme(settings.CRCZP_SERVICE_CONFIG.stack_name_prefix)
    failed: dict[str, str] = {}
    sources = _find_stack_sources(stack_names, failed)
    removed: dict[str, list[str]] = {}
    skipped: list[str] = []
    for stack_name, found_by in sorted(sources.items()):
        if _is_owned(stack_names, stack_name):
            skipped.append(stack_name)
            continue
        if dry_run:
            LOG.info('leftover_stack_would_be_removed', stack_name=stack_name, found_by=found_by)
            removed[stack_name] = found_by
            continue
        error = remove_stack(stack_name)
        if error:
            LOG.error('leftover_stack_removal_failed', stack_name=stack_name, error=error)
            failed[stack_name] = error
        else:
            LOG.info('leftover_stack_removed', stack_name=stack_name, found_by=found_by)
            removed[stack_name] = found_by
    return LeftoverReport(removed=removed, skipped=skipped, failed=failed)


def _find_stack_sources(
    stack_names: StackNameScheme, failed: dict[str, str]
) -> dict[str, list[str]]:
    """Map each stack name found to the places that listed it, recording failed listings."""
    sources: defaultdict[str, list[str]] = defaultdict(list)
    for source, listing in _get_listings(stack_names):
        try:
            names = listing()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOG.exception('leftover_stack_listing_failed', source=source)
            failed[source] = f'{type(exc).__name__}: {exc}'
            continue
        for name in names:
            sources[name].append(source)
    return dict(sources)


def _get_listings(stack_names: StackNameScheme) -> list[tuple[str, Callable[[], list[str]]]]:
    client = utils.get_terraform_client()

    def list_netbird_stacks() -> list[str]:
        netbird_client = get_netbird_client()
        if netbird_client is None:
            return []
        return list(netbird_orphans.list_netbird_objects_by_stack(netbird_client, stack_names))

    return [
        (CLOUD_SOURCE, lambda: list(client.list_stack_inventories(stack_names))),
        (WORKSPACE_SOURCE, lambda: client.list_workspaces(stack_names)),
        (NETBIRD_SOURCE, list_netbird_stacks),
        (JUMP_HOME_SOURCE, lambda: jump_proxy_cleanup.list_jump_homes(stack_names)),
    ]


def _is_owned(stack_names: StackNameScheme, stack_name: str) -> bool:
    """Whether an allocation unit of the stack exists."""
    stack = stack_names.require(stack_name)
    return SandboxAllocationUnit.objects.filter(pk=stack.unit_id, pool_id=stack.pool_id).exists()
