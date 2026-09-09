"""Build AWS VPC Traffic Mirroring render structures from the resolved forwarding rule.

The Terraform template renders network forwarding (port mirroring) purely from the
structures produced here, so all name derivation and session numbering lives in Python
where it is unit-testable.
"""

from __future__ import annotations

from typing import NamedTuple

from crczp.cloud_commons import NetworkForwarding

_NAME_SEP = '-'

# A forwarding direction (relative to the source interface) maps to the AWS filter-rule
# traffic directions that must be accepted.
_DIRECTION_TO_AWS: dict[str, tuple[str, ...]] = {
    'in': ('ingress',),
    'out': ('egress',),
    'both': ('ingress', 'egress'),
}


class MirrorTarget(NamedTuple):
    """A traffic mirror target pointing at a destination ENI."""

    name: str
    dest_port_name: str


class MirrorFilterRule(NamedTuple):
    """A single accept rule inside a traffic mirror filter."""

    name: str
    traffic_direction: str
    rule_number: int


class MirrorFilter(NamedTuple):
    """A traffic mirror filter with its per-direction accept rules."""

    name: str
    rules: list[MirrorFilterRule]


class MirrorSession(NamedTuple):
    """A traffic mirror session mirroring one source ENI to a target through a filter."""

    name: str
    source_port_name: str
    target_name: str
    filter_name: str
    session_number: int


class TrafficMirrorPlan(NamedTuple):
    """Everything the Terraform template needs to render network forwarding."""

    target: MirrorTarget | None
    mirror_filter: MirrorFilter | None
    sessions: list[MirrorSession]


def build_traffic_mirror_plan(
    network_forwarding: NetworkForwarding | None, resource_prefix: str
) -> TrafficMirrorPlan:
    """
    Build the render-ready VPC Traffic Mirroring plan from the resolved forwarding rule.

    A topology declares at most one rule, so the plan holds one target (the destination ENI)
    and one filter, whose ingress/egress accept rules match the rule's direction. Each source
    ENI gets a mirror session with a session number unique to that ENI.
    """

    def prefixed(name: str) -> str:
        return f'{resource_prefix}{_NAME_SEP}{name}'

    if network_forwarding is None:
        return TrafficMirrorPlan(target=None, mirror_filter=None, sessions=[])

    rule = network_forwarding
    dest_name = rule.destination.name
    target = MirrorTarget(name=prefixed(f'tmt-{dest_name}'), dest_port_name=prefixed(dest_name))
    mirror_filter = MirrorFilter(
        name=prefixed('tmf'),
        rules=[
            MirrorFilterRule(
                name=prefixed(f'tmfr-{direction}'),
                traffic_direction=direction,
                rule_number=rule_number,
            )
            for rule_number, direction in enumerate(_DIRECTION_TO_AWS[rule.direction], start=1)
        ],
    )

    sessions: list[MirrorSession] = []
    session_numbers: dict[str, int] = {}
    for src_index, source in enumerate(rule.sources):
        session_numbers[source.name] = session_numbers.get(source.name, 0) + 1
        sessions.append(
            MirrorSession(
                name=prefixed(f'tms-{src_index}'),
                source_port_name=prefixed(source.name),
                target_name=target.name,
                filter_name=mirror_filter.name,
                session_number=session_numbers[source.name],
            )
        )

    return TrafficMirrorPlan(target=target, mirror_filter=mirror_filter, sessions=sessions)
