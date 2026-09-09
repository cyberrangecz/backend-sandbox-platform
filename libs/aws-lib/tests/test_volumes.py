"""Tests for per-volume block-device rendering on AWS instances."""

from pytest_mock import MockerFixture

from crczp.aws_driver.aws_client import CrczpAwsClient
from crczp.cloud_commons import TopologyInstance, TransformationConfiguration
from crczp.topology_definition.models import TopologyDefinition

_TOPOLOGY = """
name: volumes-definition
hosts:
  - name: server
    base_box: { image: ami-0abc, mgmt_user: debian }
    flavor: t3.small
    volumes:
      - size: 20
      - size: 30
        image: snap-0data
      - size: 40
  - name: home
    base_box: { image: ami-0abc, mgmt_user: debian }
    flavor: t3.small
routers:
  - name: server-router
    base_box: { image: ami-0abc, mgmt_user: debian }
    flavor: t3.small
networks:
  - name: server-switch
    cidr: 10.10.20.0/24
net_mappings:
  - host: server
    network: server-switch
    ip: 10.10.20.5
  - host: home
    network: server-switch
    ip: 10.10.20.6
router_mappings:
  - router: server-router
    network: server-switch
    ip: 10.10.20.1
groups: []
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


def _instance_block(template: str, name: str) -> str:
    start = template.index(f'resource "aws_instance" "{name}" {{')
    return template[start : template.index('\n}', start)]


def test_volumes_render_block_devices(mocker: MockerFixture) -> None:
    """A host with volumes gets a sized root device plus one EBS device per extra volume.

    'server' declares a boot volume (sizes the AMI-backed root), an extra volume from an EBS
    snapshot, and a blank extra volume.
    """
    client = _client(mocker)
    template = client.create_terraform_template(_instance(_TOPOLOGY), resource_prefix='stack-p1-s2')
    server = _instance_block(template, 'stack-p1-s2-server')

    # Boot disk stays the AMI; volumes[0] only sizes the root device.
    assert 'ami = "ami-0abc"' in server
    assert 'root_block_device {\n        volume_size = 20' in server
    # Extra volume from its own snapshot.
    assert 'device_name = "/dev/sdf"' in server
    assert 'snapshot_id = "snap-0data"' in server
    # Blank extra volume: an EBS device with no snapshot source.
    assert 'device_name = "/dev/sdg"' in server
    assert server.count('ebs_block_device {') == 2
    assert server.count('snapshot_id') == 1
    assert 'volume_size = 30' in server
    assert 'volume_size = 40' in server

    # A host without volumes has no block devices (root stays implicit from the AMI).
    home = _instance_block(template, 'stack-p1-s2-home')
    assert 'block_device' not in home
