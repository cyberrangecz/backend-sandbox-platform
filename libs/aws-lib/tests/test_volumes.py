"""Tests for per-volume block-device rendering on AWS instances."""

import re
from collections.abc import Callable

from crczp.aws_driver.aws_client import CrczpAwsClient
from crczp.cloud_commons import TopologyInstance, hcl_string
from crczp.cloud_commons.topology_elements import Host
from crczp.topology_definition.validators import MAX_VOLUMES

_PREFIX = 'stack-p1-s2'

_SERVER_VOLUMES = """
    volumes:
      - size: 20
      - size: 30
        image: snap-0data
      - size: 40
"""


def test_volumes_render_block_devices(
    aws_client: CrczpAwsClient,
    topology: Callable[..., TopologyInstance],
    resource_block: Callable[[str, str, str], str],
) -> None:
    """A host with volumes gets a sized root device plus one EBS device per extra volume."""
    template = aws_client.create_terraform_template(
        topology(server_volumes=_SERVER_VOLUMES), resource_prefix=_PREFIX
    )
    server = resource_block(template, 'aws_instance', f'{_PREFIX}-server')

    # AWS keeps booting from the AMI; volumes[0] only sizes the root device.
    assert 'ami = "ami-0abc"' in server
    assert 'root_block_device { volume_size = 20' in server

    first, second = server.split('ebs_block_device {')[1:]
    assert 'device_name = "/dev/sdf"' in first
    assert 'snapshot_id = "snap-0data"' in first
    assert 'volume_size = 30' in first
    assert 'device_name = "/dev/sdg"' in second
    assert 'snapshot_id' not in second
    assert 'volume_size = 40' in second

    assert 'block_device' not in resource_block(template, 'aws_instance', f'{_PREFIX}-monitoring')


def test_max_volumes_use_consecutive_device_names(
    aws_client: CrczpAwsClient,
    topology: Callable[..., TopologyInstance],
    resource_block: Callable[[str, str, str], str],
) -> None:
    """The maximum number of volumes maps onto consecutive device names starting at /dev/sdf."""
    volumes = '    volumes:' + ''.join(f'\n      - size: {10 + i}' for i in range(MAX_VOLUMES))
    template = aws_client.create_terraform_template(
        topology(server_volumes=volumes), resource_prefix=_PREFIX
    )
    server = resource_block(template, 'aws_instance', f'{_PREFIX}-server')

    devices = re.findall(r'device_name = "([^"]*)"', server)
    assert devices == [f'/dev/sd{chr(ord("f") + i)}' for i in range(MAX_VOLUMES - 1)]


_INJECTION_PAYLOAD = '"\n}\nresource "aws_instance" "inj" {}\n${file("/etc/hostname")}'


def test_volume_snapshot_cannot_inject_terraform(
    aws_client: CrczpAwsClient,
    topology: Callable[..., TopologyInstance],
    resource_block: Callable[[str, str, str], str],
) -> None:
    """A hostile snapshot id stays inside its string literal.

    Snapshot ids are free text from the topology definition. Left unescaped, a quote closes the
    string, a brace closes the block, and `${...}` would be evaluated by OpenTofu.
    """
    instance = topology(server_volumes=_SERVER_VOLUMES)
    server = instance.get_node('server')
    assert isinstance(server, Host)
    server.volumes[1].image = _INJECTION_PAYLOAD

    template = aws_client.create_terraform_template(instance, resource_prefix=_PREFIX)

    server_block = resource_block(template, 'aws_instance', f'{_PREFIX}-server')
    assert f'snapshot_id = {hcl_string(_INJECTION_PAYLOAD)} ' in server_block
    assert re.findall(r'^resource "aws_instance" "inj"', template, re.MULTILINE) == []
    assert '$${file(' in template
    assert not re.findall(r'(?<!\$)\$\{', template)


def test_ami_and_instance_type_cannot_inject_terraform(
    aws_client: CrczpAwsClient,
    topology: Callable[..., TopologyInstance],
    resource_block: Callable[[str, str, str], str],
) -> None:
    """The AMI and instance type are escaped like the snapshot id."""
    instance = topology()
    monitoring = instance.get_node('monitoring')
    assert isinstance(monitoring, Host)
    monitoring.base_box.image = _INJECTION_PAYLOAD
    monitoring.flavor = _INJECTION_PAYLOAD

    template = aws_client.create_terraform_template(instance, resource_prefix=_PREFIX)

    monitoring_block = resource_block(template, 'aws_instance', f'{_PREFIX}-monitoring')
    assert f'ami = {hcl_string(_INJECTION_PAYLOAD)} ' in monitoring_block
    assert f'instance_type = {hcl_string(_INJECTION_PAYLOAD)} ' in monitoring_block
    assert re.findall(r'^resource "aws_instance" "inj"', template, re.MULTILINE) == []
    assert not re.findall(r'(?<!\$)\$\{', template)


_AMI = {
    'ImageId': 'ami-0abc',
    'Name': 'debian',
    'PlatformDetails': 'Linux/UNIX',
    'Public': True,
    'State': 'available',
    'CreationDate': '2024-01-01T00:00:00.000Z',
    'RootDeviceName': '/dev/xvda',
}


def test_image_min_disk_is_the_ami_root_volume_size() -> None:
    """The root device mapping sizes min_disk; other mappings are ignored."""
    ami = {
        **_AMI,
        'BlockDeviceMappings': [
            {'DeviceName': '/dev/sdb', 'Ebs': {'VolumeSize': 99}},
            {'DeviceName': '/dev/xvda', 'Ebs': {'VolumeSize': 8}},
        ],
    }
    assert CrczpAwsClient._map_aws_image(ami).min_disk == 8  # pylint: disable=protected-access


def test_image_min_disk_is_zero_without_an_ebs_root_mapping() -> None:
    """Instance-store AMIs and partial responses report no minimum."""
    map_image = CrczpAwsClient._map_aws_image  # pylint: disable=protected-access
    assert map_image(_AMI).min_disk == 0
    instance_store = {**_AMI, 'BlockDeviceMappings': [{'DeviceName': '/dev/xvda'}]}
    assert map_image(instance_store).min_disk == 0
