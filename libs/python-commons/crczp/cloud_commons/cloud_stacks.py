"""Cloud resources identified by the stack that created them, and their ordered deletion."""

import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class StackName:
    """A sandbox stack name: the deployment prefix, the pool id and the allocation unit id."""

    prefix: str
    pool_id: int
    unit_id: int

    def __str__(self) -> str:
        return f'{self.prefix}-p{self.pool_id:010d}-s{self.unit_id:010d}'

    @property
    def short(self) -> str:
        """The name with the ids unpadded, as NetBird object names carry it."""
        return f'{self.prefix}-p{self.pool_id}-s{self.unit_id}'


@dataclass(frozen=True)
class CloudResource:
    """A single resource of a cloud project."""

    kind: str
    resource_id: str
    name: str


@dataclass(frozen=True)
class TaggedResource:
    """A cloud resource together with the stack tags the cloud reports for it."""

    resource: CloudResource
    tags: tuple[str, ...] = ()


class StackNameScheme:
    """
    Recognises the stack names of one deployment and nothing else: a name with another
    prefix, a pool prefix, a bare prefix or an empty name is never read as a stack name.

    :param prefix: The deployment's stack name prefix
    :raise ValueError: The prefix is empty
    """

    def __init__(self, prefix: str) -> None:
        if not prefix:
            raise ValueError('The stack name prefix must not be empty')
        self.prefix = prefix
        escaped_prefix = re.escape(prefix)
        self._full_pattern = re.compile(
            rf'{escaped_prefix}-p(?P<pool_id>\d{{10}})-s(?P<unit_id>\d{{10}})', re.ASCII
        )
        self._short_pattern = re.compile(
            rf'{escaped_prefix}-p(?P<pool_id>[1-9]\d{{0,9}})-s(?P<unit_id>[1-9]\d{{0,9}})',
            re.ASCII,
        )

    def parse(self, name: str) -> StackName | None:
        """
        Read a full stack name.

        :param name: The whole name to read
        :return: The stack, or None when the name is not a full stack name of this deployment
        """
        return self._stack_of(self._full_pattern.fullmatch(name))

    def parse_short(self, name: str) -> StackName | None:
        """
        Read a stack name in its unpadded form.

        :param name: The whole name to read
        :return: The stack, or None when the name is not a short stack name of this deployment
        """
        return self._stack_of(self._short_pattern.fullmatch(name))

    def require(self, name: str) -> StackName:
        """
        Read a full stack name that has to be valid.

        :param name: The whole name to read
        :return: The stack
        :raise ValueError: The name is not a full stack name of this deployment
        """
        stack = self.parse(name)
        if stack is None:
            raise ValueError(f'Not a stack name of this deployment: {name!r}')
        return stack

    def owner_of(self, name: str, separator: str = '-') -> StackName | None:
        """
        Get the stack whose full name opens a resource name, followed by the separator.

        :param name: The resource name
        :param separator: The text that has to follow the stack name
        :return: The stack, or None when the name does not open with a stack name
        """
        return self._stack_of(_followed_by(self._full_pattern.match(name), name, separator))

    def short_owner_of(self, name: str, separator: str = '-') -> StackName | None:
        """
        Get the stack whose short name opens an object name, followed by the separator.

        :param name: The object name
        :param separator: The text that has to follow the short stack name
        :return: The stack, or None when the name does not open with a short stack name
        """
        return self._stack_of(_followed_by(self._short_pattern.match(name), name, separator))

    def owner_of_resource(self, tagged: TaggedResource) -> StackName | None:
        """
        Get the stack a cloud resource belongs to, from a stack tag or else from its name.

        :param tagged: The resource with its tags
        :return: The stack, or None when the resource belongs to no stack of this deployment
        """
        for tag in tagged.tags:
            stack = self.parse(tag)
            if stack is not None:
                return stack
        return self.owner_of(tagged.resource.name)

    def select_stack_names(self, names: Iterable[str], source: str) -> list[str]:
        """
        Keep the full stack names of this deployment among listed names, reporting each
        malformed name as ``report_malformed`` does.

        :param names: The names found while listing
        :param source: What was listed, for the log entry
        :return: The names that are full stack names, in the order given
        """
        selected = []
        for name in names:
            if self.parse(name) is not None:
                selected.append(name)
            else:
                self.report_malformed(name, source)
        return selected

    def report_malformed(self, name: str, source: str) -> bool:
        """
        Log at error level a name that opens with the deployment prefix and a hyphen but
        carries no parsable pool and unit id. The name belongs to no stack and is left alone.

        :param name: The name found while listing
        :param source: What was listed, for the log entry
        :return: Whether the name is malformed in that way
        """
        malformed = (
            name.startswith(f'{self.prefix}-')
            and self.parse(name) is None
            and self.owner_of(name) is None
            and self.short_owner_of(name) is None
        )
        if malformed:
            LOG.error(
                'Leaving alone a name of %s with the stack prefix but no parsable pool or unit '
                'id: %r',
                source,
                name,
            )
        return malformed

    def _stack_of(self, match: re.Match[str] | None) -> StackName | None:
        """Build the stack of a stack-name match."""
        if match is None:
            return None
        return StackName(self.prefix, int(match['pool_id']), int(match['unit_id']))


def _followed_by(match: re.Match[str] | None, name: str, separator: str) -> re.Match[str] | None:
    """Keep a match only when the separator follows it in the name."""
    if match is None or not name.startswith(separator, match.end()):
        return None
    return match


@dataclass
class StackInventory:
    """The cloud resources of one stack and the RAM, in GB, held by its servers."""

    resources: list[CloudResource] = field(default_factory=list)
    ram_gb: float = 0.0


class DeletionOutcome(StrEnum):
    """How the deletion of one cloud resource ended."""

    DELETED = 'deleted'
    ALREADY_GONE = 'already_gone'
    FAILED = 'failed'


@dataclass(frozen=True)
class ResourceDeletion:
    """The outcome of deleting one cloud resource, with the error of a failed one."""

    resource: CloudResource
    outcome: DeletionOutcome
    error: str = ''

    @property
    def gone(self) -> bool:
        """Whether the resource no longer exists, deleted now or before."""
        return self.outcome is not DeletionOutcome.FAILED


@dataclass(frozen=True)
class ResourceKind:
    """
    How the resources of one kind are deleted.

    :param name: The kind, as discovery sets it on each resource
    :param delete: Deletes one resource by its ID
    :param settle: For a kind deleted asynchronously, waits for the given resources to
                   disappear and returns an error per ID of a resource still present
    """

    name: str
    delete: Callable[[str], object]
    settle: Callable[[Sequence[CloudResource]], Mapping[str, str]] | None = None


def group_by_stack(
    tagged_resources: Iterable[TaggedResource],
    ram_gb_by_resource_id: Mapping[str, float],
    stack_names: StackNameScheme,
) -> dict[str, StackInventory]:
    """
    Group resources by the stack of this deployment they belong to, leaving out the rest.

    :param tagged_resources: Resources with their tags
    :param ram_gb_by_resource_id: RAM in GB held by a resource, for the resources that hold any
    :param stack_names: The stack names of this deployment
    :return: Inventory of each stack, keyed by full stack name
    """
    inventories: dict[str, StackInventory] = {}
    for tagged in tagged_resources:
        stack = stack_names.owner_of_resource(tagged)
        if stack is None:
            stack_names.report_malformed(tagged.resource.name, 'a cloud resource')
            continue
        inventory = inventories.setdefault(str(stack), StackInventory())
        inventory.resources.append(tagged.resource)
        inventory.ram_gb += ram_gb_by_resource_id.get(tagged.resource.resource_id, 0.0)
    for inventory in inventories.values():
        inventory.ram_gb = round(inventory.ram_gb, 1)
    return inventories


def select_stack(
    tagged_resources: Iterable[TaggedResource], stack_name: str, stack_names: StackNameScheme
) -> list[CloudResource]:
    """
    Pick the resources of one stack, by the same rule that groups them.

    :param tagged_resources: Resources with their tags
    :param stack_name: The full stack name
    :param stack_names: The stack names of this deployment
    :return: The resources of the stack
    :raise ValueError: The name is not a full stack name of this deployment
    """
    stack = stack_names.require(stack_name)
    return [
        tagged.resource
        for tagged in tagged_resources
        if stack_names.owner_of_resource(tagged) == stack
    ]


def delete_in_order(
    resources: Iterable[CloudResource],
    kinds: Sequence[ResourceKind],
    is_not_found: Callable[[Exception], bool],
) -> list[ResourceDeletion]:
    """
    Delete resources kind by kind, carrying on after any failure. A resource of a kind
    with no entry in kinds is reported as failed.

    :param resources: Resources to delete
    :param kinds: The kinds, dependents first
    :param is_not_found: Tells whether a deletion error means the resource is already gone
    :return: One result per resource, in deletion order
    """
    resources_by_kind: defaultdict[str, list[CloudResource]] = defaultdict(list)
    for resource in resources:
        resources_by_kind[resource.kind].append(resource)
    results: list[ResourceDeletion] = []
    for kind in kinds:
        kind_results = [
            _delete_resource(kind, resource, is_not_found)
            for resource in resources_by_kind.pop(kind.name, [])
        ]
        results.extend(_settle(kind, kind_results))
    results.extend(
        ResourceDeletion(resource, DeletionOutcome.FAILED, f'No deletion for kind {resource.kind}')
        for unknown_kind_resources in resources_by_kind.values()
        for resource in unknown_kind_resources
    )
    return results


def _delete_resource(
    kind: ResourceKind, resource: CloudResource, is_not_found: Callable[[Exception], bool]
) -> ResourceDeletion:
    """Delete one resource, turning any error into its result."""
    try:
        kind.delete(resource.resource_id)
    except Exception as error:  # pylint: disable=broad-exception-caught
        if is_not_found(error):
            return ResourceDeletion(resource, DeletionOutcome.ALREADY_GONE)
        return ResourceDeletion(resource, DeletionOutcome.FAILED, _describe(error))
    return ResourceDeletion(resource, DeletionOutcome.DELETED)


def _settle(kind: ResourceKind, results: list[ResourceDeletion]) -> list[ResourceDeletion]:
    """Wait for the resources deleted now to disappear; one still present counts as failed."""
    deleted = [result.resource for result in results if result.outcome is DeletionOutcome.DELETED]
    if kind.settle is None or not deleted:
        return results
    try:
        unsettled: Mapping[str, str] = kind.settle(deleted)
    except Exception as error:  # pylint: disable=broad-exception-caught
        message = f'Deletion not confirmed: {_describe(error)}'
        unsettled = {resource.resource_id: message for resource in deleted}
    return [
        replace(
            result, outcome=DeletionOutcome.FAILED, error=unsettled[result.resource.resource_id]
        )
        if result.outcome is DeletionOutcome.DELETED and result.resource.resource_id in unsettled
        else result
        for result in results
    ]


def _describe(error: Exception) -> str:
    """The error's message, or its type name when it has none."""
    return str(error) or type(error).__name__
