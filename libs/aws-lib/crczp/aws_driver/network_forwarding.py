"""AWS VPC Traffic Mirroring: rule validation and filter-rule directions."""

from crczp.cloud_commons import InvalidTopologyDefinition, NetworkForwarding
from crczp.topology_definition.models import ForwardingDirection

# The AWS limit of sources per mirror target for the instance types the driver offers.
MAX_SOURCES = 10

# The only mirroring-capable families among the t* types get_flavors_dict offers (t2 is not).
MIRRORING_FAMILIES = ('t3', 't3a', 't4g')

_DIRECTION_TO_AWS: dict[str, tuple[str, ...]] = {
    ForwardingDirection.IN: ('ingress',),
    ForwardingDirection.OUT: ('egress',),
    ForwardingDirection.BOTH: ('ingress', 'egress'),
}


def traffic_directions(rule: NetworkForwarding | None) -> tuple[str, ...]:
    """Return the AWS filter-rule traffic directions that mirror the rule's direction."""
    return _DIRECTION_TO_AWS[rule.direction] if rule else ()


def validate_network_forwarding(rule: NetworkForwarding | None) -> None:
    """
    Raise when AWS cannot mirror the rule's sources to its destination.

    :raise InvalidTopologyDefinition: on a source outside the destination's network, more than
        MAX_SOURCES sources, or a source instance type that does not support Traffic Mirroring.
    """
    if rule is None:
        return
    if len(rule.sources) > MAX_SOURCES:
        raise InvalidTopologyDefinition(
            f'network_forwarding has {len(rule.sources)} sources but AWS Traffic Mirroring '
            f'allows at most {MAX_SOURCES} per mirror target.'
        )
    destination = rule.destination
    for source in rule.sources:
        # Each topology network is its own VPC with no route to the target.
        if source.network != destination.network:
            raise InvalidTopologyDefinition(
                f'network_forwarding source "{source.node.name}" is on network '
                f'"{source.network.name}", but on AWS every source must be on the destination '
                f'network "{destination.network.name}".'
            )
        flavor = source.node.flavor
        if flavor.split('.')[0] not in MIRRORING_FAMILIES:
            raise InvalidTopologyDefinition(
                f'network_forwarding source "{source.node.name}" has instance type "{flavor}", '
                f'which does not support AWS Traffic Mirroring; use one of the '
                f'{", ".join(MIRRORING_FAMILIES)} families.'
            )
