"""Tests for AWS VPC Traffic Mirroring (network forwarding) template generation."""

import re

from pytest_mock import MockerFixture

from crczp.aws_driver.aws_client import CrczpAwsClient
from crczp.cloud_commons import TopologyInstance, TransformationConfiguration
from crczp.topology_definition.models import TopologyDefinition

_BASE_TOPOLOGY = """
name: forwarding-definition
hosts:
  - name: server
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: t3.small
  - name: monitoring
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: t3.small
routers:
  - name: server-router
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: t3.small
networks:
  - name: server-switch
    cidr: 10.10.20.0/24
  - name: monitoring-switch
    cidr: 10.10.40.0/24
net_mappings:
  - host: server
    network: server-switch
    ip: 10.10.20.5
  # The first interface of a host carries its default route, so the mirror destination
  # below has to be a second, dedicated one.
  - host: monitoring
    network: server-switch
    ip: 10.10.20.6
  - host: monitoring
    network: monitoring-switch
    ip: 10.10.40.5
router_mappings:
  - router: server-router
    network: server-switch
    ip: 10.10.20.1
  - router: server-router
    network: monitoring-switch
    ip: 10.10.40.1
groups: []
"""

_FORWARDING = """
network_forwarding:
  sources:
    - { host: server, network: server-switch }
  destination: { host: monitoring, network: monitoring-switch }
  direction: both
"""


def _trc() -> TransformationConfiguration:
    return TransformationConfiguration(
        man_image='man-image',
        man_flavor='t3.small',
        man_user='debian',
    )


def _client(mocker: MockerFixture) -> CrczpAwsClient:
    mocker.patch('crczp.aws_driver.aws_client.boto3')
    return CrczpAwsClient(
        aws_access_key='key',
        aws_secret_key='secret',
        region='eu-central-1',
        base_vpc_name='Base Net',
        base_subnet_name='Base Subnet',
        availability_zone='eu-central-1a',
        trc=_trc(),
    )


def _instance(yaml_str: str) -> TopologyInstance:
    return TopologyInstance(TopologyDefinition.load(yaml_str), _trc())


# OpenTofu rejects `}data "x" "y" {` with "Missing newline after block definition", and a
# single Jinja whitespace-control marker is enough to produce it.
_BLOCK_WELD = re.compile(r'^\s*\}\s*\S')


def _assert_blocks_newline_separated(template: str) -> None:
    """Assert every block in a rendered template closes on a line of its own."""
    welded = [
        (n, line) for n, line in enumerate(template.splitlines(), 1) if _BLOCK_WELD.match(line)
    ]
    assert not welded, f'block definition not terminated by a newline: {welded}'


def test_forwarding_emits_mirror_resources(mocker: MockerFixture) -> None:
    """A definition with network_forwarding emits target/filter/rules/session."""
    client = _client(mocker)
    template = client.create_terraform_template(
        _instance(_BASE_TOPOLOGY + _FORWARDING),
        resource_prefix='stack-p1-s2',
    )

    assert 'resource "aws_ec2_traffic_mirror_target" "stack-p1-s2-tmt-' in template
    assert 'resource "aws_ec2_traffic_mirror_filter" "stack-p1-s2-tmf"' in template
    assert 'resource "aws_ec2_traffic_mirror_filter_rule" "stack-p1-s2-tmfr-ingress"' in template
    assert 'resource "aws_ec2_traffic_mirror_filter_rule" "stack-p1-s2-tmfr-egress"' in template
    # direction "both" -> both ingress and egress accept rules.
    assert 'traffic_direction        = "ingress"' in template
    assert 'traffic_direction        = "egress"' in template
    assert 'rule_action              = "accept"' in template
    # one session for the single source ENI, session_number starts at 1.
    assert 'resource "aws_ec2_traffic_mirror_session" "stack-p1-s2-tms-0"' in template
    assert 'session_number           = 1' in template
    assert 'network_interface_id     = aws_network_interface.stack-p1-s2-' in template
    _assert_blocks_newline_separated(template)


def test_no_forwarding_omits_mirror_resources(mocker: MockerFixture) -> None:
    """A definition without network_forwarding emits no traffic-mirror resources."""
    client = _client(mocker)
    template = client.create_terraform_template(
        _instance(_BASE_TOPOLOGY),
        resource_prefix='stack-p1-s2',
    )

    assert 'aws_ec2_traffic_mirror' not in template
    _assert_blocks_newline_separated(template)
