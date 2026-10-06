"""Tests for AWS VPC Traffic Mirroring (network forwarding) validation and templating."""

import re
from collections.abc import Callable

import pytest

from crczp.aws_driver.aws_client import CrczpAwsClient
from crczp.aws_driver.network_forwarding import traffic_directions
from crczp.cloud_commons import InvalidTopologyDefinition, TopologyInstance

_FORWARDING = """
network_forwarding:
  sources:
    - { node: server, network: server-switch }
  destination: { node: monitoring, network: server-switch }
  direction: both
"""

_PREFIX = 'stack-p1-s2'

# Link numbering of the base topology in conftest: server and monitoring on server-switch, then
# the router.
_SERVER_ENI = f'{_PREFIX}-link-5'
_MONITORING_ENI = f'{_PREFIX}-link-7'
_ROUTER_ENI = f'{_PREFIX}-link-10'

_TARGET = f'{_PREFIX}-tmt-link-7'
_FILTER = f'{_PREFIX}-tmf'


def _many_sources_yaml(count: int) -> str:
    """Build a topology YAML mirroring ``count`` t3 hosts to a destination on their network."""
    names = ['monitoring', *(f'source-{i}' for i in range(count))]
    hosts = ''.join(
        f"""
  - name: {name}
    base_box: {{ image: debian-12-x86_64, mgmt_user: debian }}
    flavor: t3.small"""
        for name in names
    )
    mappings = ''.join(
        f"""
  - host: {name}
    network: switch
    ip: 10.10.20.{10 + i}"""
        for i, name in enumerate(names)
    )
    sources = ''.join(f'\n    - {{ node: source-{i}, network: switch }}' for i in range(count))
    return f"""
name: many-sources
hosts:{hosts}
routers:
  - name: router
    base_box: {{ image: debian-12-x86_64, mgmt_user: debian }}
    flavor: t3.small
networks:
  - name: switch
    cidr: 10.10.20.0/24
net_mappings:{mappings}
router_mappings:
  - router: router
    network: switch
    ip: 10.10.20.1
groups: []
network_forwarding:
  sources:{sources}
  destination: {{ node: monitoring, network: switch }}
  direction: in
"""


# OpenTofu rejects `}data "x" "y" {` with "Missing newline after block definition", and a
# single Jinja whitespace-control marker is enough to produce it.
_BLOCK_WELD = re.compile(r'^\s*\}\s*\S')


def _assert_blocks_newline_separated(template: str) -> None:
    """Assert every block in a rendered template closes on a line of its own."""
    welded = [
        (n, line) for n, line in enumerate(template.splitlines(), 1) if _BLOCK_WELD.match(line)
    ]
    assert not welded, f'block definition not terminated by a newline: {welded}'


@pytest.mark.parametrize(
    ('direction', 'expected'),
    [('in', ('ingress',)), ('out', ('egress',)), ('both', ('ingress', 'egress'))],
)
def test_traffic_directions(
    topology: Callable[..., TopologyInstance], direction: str, expected: tuple[str, ...]
) -> None:
    """The forwarding direction maps to the AWS filter-rule directions."""
    rule = topology(_FORWARDING.replace('both', direction)).get_network_forwarding()

    assert traffic_directions(rule) == expected


def test_traffic_directions_without_forwarding() -> None:
    """No rule means no filter rules."""
    assert not traffic_directions(None)


def test_source_on_another_network_is_rejected(
    aws_client: CrczpAwsClient, topology: Callable[..., TopologyInstance]
) -> None:
    """A source in another VPC would mirror into the void."""
    forwarding = """
network_forwarding:
  sources:
    - { node: server-router, network: monitoring-switch }
  destination: { node: monitoring, network: server-switch }
  direction: both
"""

    with pytest.raises(
        InvalidTopologyDefinition, match='"server-router" is on network "monitoring-switch"'
    ):
        aws_client.create_terraform_template(topology(forwarding))


def test_ten_sources_are_accepted(
    aws_client: CrczpAwsClient, topology_instance_from: Callable[[str], TopologyInstance]
) -> None:
    """The AWS limit of ten sources per target is allowed."""
    aws_client.create_terraform_template(topology_instance_from(_many_sources_yaml(10)))


def test_eleven_sources_are_rejected(
    aws_client: CrczpAwsClient, topology_instance_from: Callable[[str], TopologyInstance]
) -> None:
    """More than ten sources exceed the AWS limit per target."""
    with pytest.raises(InvalidTopologyDefinition, match='11 sources .* at most 10'):
        aws_client.create_terraform_template(topology_instance_from(_many_sources_yaml(11)))


def test_t2_source_host_is_rejected(
    aws_client: CrczpAwsClient, topology: Callable[..., TopologyInstance]
) -> None:
    """t2 instances do not support Traffic Mirroring."""
    with pytest.raises(InvalidTopologyDefinition, match='"server" has instance type "t2.micro"'):
        aws_client.create_terraform_template(topology(_FORWARDING, server_flavor='t2.micro'))


def test_t2_source_router_is_rejected(
    aws_client: CrczpAwsClient, topology: Callable[..., TopologyInstance]
) -> None:
    """The instance type check covers router sources too."""
    forwarding = _FORWARDING.replace('node: server,', 'node: server-router,')

    with pytest.raises(
        InvalidTopologyDefinition, match='"server-router" has instance type "t2.micro"'
    ):
        aws_client.create_terraform_template(topology(forwarding, router_flavor='t2.micro'))


def test_no_forwarding_omits_mirror_resources(
    aws_client: CrczpAwsClient, topology: Callable[..., TopologyInstance]
) -> None:
    """A definition without network_forwarding emits no traffic-mirror resources."""
    template = aws_client.create_terraform_template(topology(''), resource_prefix=_PREFIX)

    assert 'aws_ec2_traffic_mirror' not in template
    _assert_blocks_newline_separated(template)


def test_forwarding_emits_mirror_resources(
    aws_client: CrczpAwsClient,
    topology: Callable[..., TopologyInstance],
    resource_block: Callable[[str, str, str], str],
) -> None:
    """The target, filter, rules and session reference the right ENIs and each other."""
    template = aws_client.create_terraform_template(topology(_FORWARDING), resource_prefix=_PREFIX)

    target = resource_block(template, 'aws_ec2_traffic_mirror_target', _TARGET)
    assert f'network_interface_id = aws_network_interface.{_MONITORING_ENI}.id' in target
    assert f'depends_on = [aws_instance.{_PREFIX}-monitoring]' in target
    assert f'tags = {{ Name = "{_TARGET}" }}' in target

    mirror_filter = resource_block(template, 'aws_ec2_traffic_mirror_filter', _FILTER)
    assert 'network_services = ["amazon-dns"]' in mirror_filter
    assert f'tags = {{ Name = "{_FILTER}" }}' in mirror_filter

    for number, direction in enumerate(('ingress', 'egress'), start=1):
        rule = resource_block(
            template, 'aws_ec2_traffic_mirror_filter_rule', f'{_PREFIX}-tmfr-{direction}'
        )
        assert f'traffic_mirror_filter_id = aws_ec2_traffic_mirror_filter.{_FILTER}.id' in rule
        assert f'rule_number = {number} ' in rule
        assert 'rule_action = "accept"' in rule
        assert f'traffic_direction = "{direction}"' in rule
        assert 'tags' not in rule

    session = resource_block(template, 'aws_ec2_traffic_mirror_session', f'{_PREFIX}-tms-0')
    assert f'network_interface_id = aws_network_interface.{_SERVER_ENI}.id' in session
    assert f'traffic_mirror_target_id = aws_ec2_traffic_mirror_target.{_TARGET}.id' in session
    assert f'traffic_mirror_filter_id = aws_ec2_traffic_mirror_filter.{_FILTER}.id' in session
    assert 'session_number = 1 ' in session
    assert f'depends_on = [aws_instance.{_PREFIX}-server]' in session
    assert f'tags = {{ Name = "{_PREFIX}-tms-0" }}' in session
    _assert_blocks_newline_separated(template)


def test_forwarding_direction_selects_filter_rules(
    aws_client: CrczpAwsClient, topology: Callable[..., TopologyInstance]
) -> None:
    """Only the rule's direction gets a filter rule."""
    template = aws_client.create_terraform_template(
        topology(_FORWARDING.replace('both', 'in')), resource_prefix=_PREFIX
    )

    assert f'"{_PREFIX}-tmfr-ingress"' in template
    assert 'tmfr-egress' not in template


def test_host_and_router_sources_get_one_session_each(
    aws_client: CrczpAwsClient,
    topology: Callable[..., TopologyInstance],
    resource_block: Callable[[str, str, str], str],
) -> None:
    """Each source gets its own session on its own ENI, towards the same target and filter."""
    forwarding = _FORWARDING.replace(
        '- { node: server, network: server-switch }',
        '- { node: server, network: server-switch }\n'
        '    - { node: server-router, network: server-switch }',
    )

    template = aws_client.create_terraform_template(topology(forwarding), resource_prefix=_PREFIX)

    for index, (eni, node) in enumerate(((_SERVER_ENI, 'server'), (_ROUTER_ENI, 'server-router'))):
        session = resource_block(
            template, 'aws_ec2_traffic_mirror_session', f'{_PREFIX}-tms-{index}'
        )
        assert f'network_interface_id = aws_network_interface.{eni}.id' in session
        assert f'traffic_mirror_target_id = aws_ec2_traffic_mirror_target.{_TARGET}.id' in session
        assert f'traffic_mirror_filter_id = aws_ec2_traffic_mirror_filter.{_FILTER}.id' in session
        assert 'session_number = 1 ' in session
        assert f'depends_on = [aws_instance.{_PREFIX}-{node}]' in session
    assert f'{_PREFIX}-tms-2' not in template
