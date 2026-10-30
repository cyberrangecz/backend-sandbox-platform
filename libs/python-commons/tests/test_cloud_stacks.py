"""Tests for crczp.cloud_commons.cloud_stacks module."""

from collections.abc import Mapping, Sequence

import pytest

from crczp.cloud_commons import (
    CloudResource,
    DeletionOutcome,
    ResourceDeletion,
    ResourceKind,
    StackInventory,
    StackName,
    StackNameScheme,
    TaggedResource,
    delete_in_order,
    group_by_stack,
    select_stack,
)

STACK_NAMES = StackNameScheme('default0')
STACK = 'default0-p0000000001-s0000000001'
LONGER_STACK = 'default0-p0000000001-s0000000012'
FOREIGN_STACK = 'other-p0000000001-s0000000001'


def _tagged(kind: str, resource_id: str, name: str, *tags: str) -> TaggedResource:
    return TaggedResource(CloudResource(kind, resource_id, name), tags)


class _Gone(Exception):
    """Raised by a fake deleter for a missing resource."""


class _Refused(Exception):
    """Raised by a fake deleter for a resource the cloud will not delete."""


def _is_gone(error: Exception) -> bool:
    return isinstance(error, _Gone)


def test_stack_name_formats_the_full_and_short_forms() -> None:
    stack = StackName('default0', 1, 12)

    assert str(stack) == 'default0-p0000000001-s0000000012'
    assert stack.short == 'default0-p1-s12'


def test_scheme_parses_a_full_stack_name_of_its_prefix() -> None:
    assert STACK_NAMES.parse(STACK) == StackName('default0', 1, 1)


@pytest.mark.parametrize(
    'name',
    [
        '',
        'default0',
        'default0-p0000000001',
        f'{STACK}-net',
        FOREIGN_STACK,
        'xdefault0-p0000000001-s0000000001',
        'default0-p1-s1',
        'default0-p000000000²-s0000000001',
    ],
)
def test_scheme_refuses_what_is_not_a_full_stack_name_of_its_prefix(name: str) -> None:
    assert STACK_NAMES.parse(name) is None
    with pytest.raises(ValueError, match='Not a stack name'):
        STACK_NAMES.require(name)


def test_scheme_refuses_an_empty_prefix() -> None:
    with pytest.raises(ValueError, match='prefix'):
        StackNameScheme('')


def test_scheme_escapes_regex_characters_of_the_prefix() -> None:
    scheme = StackNameScheme('a.b')

    assert scheme.parse('a.b-p0000000001-s0000000001') is not None
    assert scheme.parse('axb-p0000000001-s0000000001') is None


def test_scheme_parses_the_short_form_without_padding() -> None:
    assert STACK_NAMES.parse_short('default0-p1-s12') == StackName('default0', 1, 12)
    assert STACK_NAMES.parse_short('default0-p01-s12') is None
    assert STACK_NAMES.parse_short(STACK) is None


def test_owner_of_needs_the_separator_after_the_stack_name() -> None:
    assert STACK_NAMES.owner_of(f'{STACK}-server') == StackName('default0', 1, 1)
    assert STACK_NAMES.owner_of(f'{LONGER_STACK}-server') == StackName('default0', 1, 12)
    assert STACK_NAMES.owner_of(STACK) is None
    assert STACK_NAMES.owner_of(f'{FOREIGN_STACK}-server') is None


def test_short_owner_of_keeps_lookalike_stacks_apart() -> None:
    assert STACK_NAMES.short_owner_of('default0-p1-s1-access') == StackName('default0', 1, 1)
    assert STACK_NAMES.short_owner_of('default0-p1-s12-access') == StackName('default0', 1, 12)
    assert STACK_NAMES.short_owner_of('default0-p1-s1 host route', ' ') == StackName(
        'default0', 1, 1
    )
    assert STACK_NAMES.short_owner_of('other-p1-s1-access') is None


def test_owner_of_resource_prefers_a_stack_tag_and_ignores_foreign_tags() -> None:
    assert STACK_NAMES.owner_of_resource(_tagged('port', 'p', '', 'unrelated', STACK)) == (
        StackName('default0', 1, 1)
    )
    assert STACK_NAMES.owner_of_resource(_tagged('port', 'p', '', FOREIGN_STACK)) is None


def test_select_stack_keeps_the_resources_of_that_stack_only() -> None:
    tagged_resources = [
        _tagged('network', 'n1', f'{STACK}-net'),
        _tagged('floating_ip', 'f1', '10.0.0.1', STACK),
        _tagged('network', 'n2', f'{LONGER_STACK}-net'),
        _tagged('network', 'n3', 'base-net'),
        _tagged('network', 'n4', f'{FOREIGN_STACK}-net'),
    ]

    selected = select_stack(tagged_resources, STACK, STACK_NAMES)

    assert [resource.resource_id for resource in selected] == ['n1', 'f1']


@pytest.mark.parametrize(
    'stack_name', ['', 'default0', 'default0-p0000000001', 'sandbox', FOREIGN_STACK]
)
def test_select_stack_refuses_what_is_not_a_stack_name(stack_name: str) -> None:
    tagged_resources = [_tagged('security_group', 'g1', 'sandbox-internal-sg')]

    with pytest.raises(ValueError, match='Not a stack name'):
        select_stack(tagged_resources, stack_name, STACK_NAMES)


def test_group_by_stack_sums_ram_per_stack_and_skips_foreign_resources() -> None:
    tagged_resources = [
        _tagged('server', 's1', f'{STACK}-a'),
        _tagged('server', 's2', f'{STACK}-b'),
        _tagged('server', 's3', f'{LONGER_STACK}-a'),
        _tagged('server', 's4', 'base-server'),
        _tagged('server', 's5', f'{FOREIGN_STACK}-a'),
        _tagged('port', 'p1', '', STACK),
    ]

    inventories = group_by_stack(
        tagged_resources, {'s1': 2.0, 's2': 4.1, 's3': 8.0, 's4': 99.0, 's5': 1.0}, STACK_NAMES
    )

    assert set(inventories) == {STACK, LONGER_STACK}
    assert inventories[STACK].ram_gb == 6.1
    assert [resource.resource_id for resource in inventories[STACK].resources] == ['s1', 's2', 'p1']
    assert inventories[LONGER_STACK] == StackInventory(
        [CloudResource('server', 's3', f'{LONGER_STACK}-a')], 8.0
    )


def test_delete_in_order_runs_dependents_first_and_tells_outcomes_apart() -> None:
    calls: list[str] = []

    def refuse_network(resource_id: str) -> None:
        calls.append(resource_id)
        raise _Refused('in use')

    def record_port(resource_id: str) -> None:
        calls.append(resource_id)

    def miss_server(resource_id: str) -> None:
        calls.append(resource_id)
        raise _Gone

    resources = [
        CloudResource('network', 'net', 'n'),
        CloudResource('port', 'port', 'p'),
        CloudResource('server', 'srv', 's'),
    ]

    results = delete_in_order(
        resources,
        (
            ResourceKind('server', miss_server),
            ResourceKind('port', record_port),
            ResourceKind('network', refuse_network),
        ),
        _is_gone,
    )

    assert calls == ['srv', 'port', 'net']
    assert results == [
        ResourceDeletion(resources[2], DeletionOutcome.ALREADY_GONE),
        ResourceDeletion(resources[1], DeletionOutcome.DELETED),
        ResourceDeletion(resources[0], DeletionOutcome.FAILED, 'in use'),
    ]
    assert [result.gone for result in results] == [True, True, False]


def test_delete_in_order_reports_a_kind_without_deletion_and_any_unexpected_error() -> None:
    def crash(_resource_id: str) -> None:
        raise KeyError('boom')

    unknown = CloudResource('volume', 'v1', 'v')
    crashing = CloudResource('port', 'p1', 'p')

    results = delete_in_order([unknown, crashing], (ResourceKind('port', crash),), _is_gone)

    assert results == [
        ResourceDeletion(crashing, DeletionOutcome.FAILED, "'boom'"),
        ResourceDeletion(unknown, DeletionOutcome.FAILED, 'No deletion for kind volume'),
    ]


def test_delete_in_order_settles_only_resources_deleted_now() -> None:
    settled: list[Sequence[CloudResource]] = []
    first = CloudResource('server', 'ok', 'a')
    second = CloudResource('server', 'bad', 'b')
    third = CloudResource('server', 'gone', 'c')

    def delete_server(resource_id: str) -> None:
        if resource_id == 'bad':
            raise _Refused('stuck')
        if resource_id == 'gone':
            raise _Gone

    def settle(servers: Sequence[CloudResource]) -> Mapping[str, str]:
        settled.append(servers)
        return {}

    results = delete_in_order(
        [first, second, third], (ResourceKind('server', delete_server, settle),), _is_gone
    )

    assert settled == [[first]]
    assert [result.outcome for result in results] == [
        DeletionOutcome.DELETED,
        DeletionOutcome.FAILED,
        DeletionOutcome.ALREADY_GONE,
    ]


def test_delete_in_order_reports_resources_a_settle_step_leaves_behind() -> None:
    first = CloudResource('server', 's1', 'a')
    second = CloudResource('server', 's2', 'b')
    port = CloudResource('port', 'p1', 'p')

    results = delete_in_order(
        [first, second, port],
        (
            ResourceKind('server', lambda _: None, lambda _: {'s2': 'still present'}),
            ResourceKind('port', lambda _: None),
        ),
        _is_gone,
    )

    assert results == [
        ResourceDeletion(first, DeletionOutcome.DELETED),
        ResourceDeletion(second, DeletionOutcome.FAILED, 'still present'),
        ResourceDeletion(port, DeletionOutcome.DELETED),
    ]


def test_delete_in_order_carries_on_when_a_settle_step_raises() -> None:
    server = CloudResource('server', 's1', 'a')
    port = CloudResource('port', 'p1', 'p')

    def failing_settle(_servers: Sequence[CloudResource]) -> Mapping[str, str]:
        raise ConnectionError('auth blip')

    results = delete_in_order(
        [server, port],
        (
            ResourceKind('server', lambda _: None, failing_settle),
            ResourceKind('port', lambda _: None),
        ),
        _is_gone,
    )

    assert results == [
        ResourceDeletion(server, DeletionOutcome.FAILED, 'Deletion not confirmed: auth blip'),
        ResourceDeletion(port, DeletionOutcome.DELETED),
    ]


def test_select_stack_names_keeps_full_stack_names_and_logs_a_malformed_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    names = [STACK, 'default0-pool-unit', 'base-net', FOREIGN_STACK, 'default0-']

    with caplog.at_level('ERROR'):
        selected = STACK_NAMES.select_stack_names(names, 'the listing')

    assert selected == [STACK]
    logged = [record.getMessage() for record in caplog.records]
    assert len(logged) == 2
    assert any('default0-pool-unit' in message for message in logged)
    assert all('the listing' in message for message in logged)


def test_report_malformed_leaves_foreign_names_and_stack_names_unreported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level('ERROR'):
        reported = [
            STACK_NAMES.report_malformed(name, 'the listing')
            for name in (STACK, 'default0-p0000000001-s0000000001-net', 'base-net', FOREIGN_STACK)
        ]

    assert reported == [False, False, False, False]
    assert not caplog.records


def test_group_by_stack_logs_a_resource_named_with_the_prefix_but_no_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level('ERROR'):
        inventories = group_by_stack(
            [_tagged('server', 's1', 'default0-orphan')], {'s1': 1.0}, STACK_NAMES
        )

    assert inventories == {}
    assert 'default0-orphan' in caplog.text
