"""Shared pytest fixtures for the AWS driver test suite."""

import re
from collections.abc import Callable

import pytest
from pytest_mock import MockerFixture

from crczp.aws_driver.aws_client import CrczpAwsClient
from crczp.cloud_commons import TopologyInstance, TransformationConfiguration
from crczp.topology_definition.models import TopologyDefinition


@pytest.fixture(name='trc')
def fixture_trc() -> TransformationConfiguration:
    """Create a transformation configuration."""
    return TransformationConfiguration(
        man_image='man-image',
        man_flavor='t3.small',
        man_user='debian',
    )


@pytest.fixture(name='aws_client')
def fixture_aws_client(
    mocker: MockerFixture,
    trc: TransformationConfiguration,
) -> CrczpAwsClient:
    """Create an AWS client whose boto3 session is mocked out."""
    mocker.patch('crczp.aws_driver.aws_client.boto3')
    return CrczpAwsClient(
        aws_access_key='key',
        aws_secret_key='secret',
        region='eu-central-1',
        base_vpc_name='Base Net',
        base_subnet_name='Base Subnet',
        availability_zone='eu-central-1a',
        trc=trc,
    )


@pytest.fixture(name='topology_instance_from')
def fixture_topology_instance_from(
    trc: TransformationConfiguration,
) -> Callable[[str], TopologyInstance]:
    """Return a factory building a TopologyInstance from a topology definition YAML string."""

    def build(yaml_str: str) -> TopologyInstance:
        return TopologyInstance(TopologyDefinition.load(yaml_str), trc)

    return build


# Mirroring needs the source on the destination's network: each network is its own VPC.
_BASE_TOPOLOGY = """
name: base-definition
hosts:
  - name: server
    base_box: { image: ami-0abc, mgmt_user: debian }
    flavor: SERVER_FLAVOR
SERVER_VOLUMES
  - name: monitoring
    base_box: { image: ami-0abc, mgmt_user: debian }
    flavor: t3.small
routers:
  - name: server-router
    base_box: { image: ami-0abc, mgmt_user: debian }
    flavor: ROUTER_FLAVOR
networks:
  - name: server-switch
    cidr: 10.10.20.0/24
  - name: monitoring-switch
    cidr: 10.10.40.0/24
net_mappings:
  - host: server
    network: server-switch
    ip: 10.10.20.5
  - host: monitoring
    network: monitoring-switch
    ip: 10.10.40.5
  - host: monitoring
    network: server-switch
    ip: 10.10.20.6
router_mappings:
  - router: server-router
    network: server-switch
    ip: 10.10.20.1
  - router: server-router
    network: monitoring-switch
    ip: 10.10.40.1
groups: []
"""


@pytest.fixture(name='topology')
def fixture_topology(
    topology_instance_from: Callable[[str], TopologyInstance],
) -> Callable[..., TopologyInstance]:
    """Return a factory for the base topology with the given extra YAML, flavors and volumes."""

    def build(
        extra: str = '',
        server_flavor: str = 't3.small',
        router_flavor: str = 't3.small',
        server_volumes: str = '',
    ) -> TopologyInstance:
        yaml_str = (
            _BASE_TOPOLOGY
            .replace('SERVER_FLAVOR', server_flavor)
            .replace('ROUTER_FLAVOR', router_flavor)
            .replace('SERVER_VOLUMES', server_volumes)
        )
        return topology_instance_from(yaml_str + extra)

    return build


@pytest.fixture(name='resource_block')
def fixture_resource_block() -> Callable[[str, str, str], str]:
    """Return a function extracting one resource block of a rendered template, squashed."""

    def extract(template: str, resource_type: str, name: str) -> str:
        match = re.search(
            rf'^resource "{resource_type}" "{re.escape(name)}" \{{.*?^\}}$',
            template,
            re.MULTILINE | re.DOTALL,
        )
        assert match, f'no {resource_type} {name} in the template'
        return re.sub(r'\s+', ' ', match.group())

    return extract
