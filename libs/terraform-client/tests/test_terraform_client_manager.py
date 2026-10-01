"""Tests for resource lookups in CrczpTerraformClientManager."""

from unittest.mock import MagicMock, patch

import pytest

from crczp.terraform_driver.terraform_client_manager import CrczpTerraformClientManager


def _resource(type_: str, name: str, resource_id: str) -> dict:
    return {
        'mode': 'managed',
        'type': type_,
        'name': name,
        'instances': [{'attributes': {'id': resource_id}}],
    }


@pytest.fixture(name='manager')
def manager_fixture(tmp_path) -> CrczpTerraformClientManager:
    """A manager whose cloud client, configuration and backend are mocks."""
    return CrczpTerraformClientManager(str(tmp_path), MagicMock(), MagicMock(), None, MagicMock())


def test_get_resource_id_ignores_non_instance_with_node_name(manager) -> None:
    """A later non-instance resource with the node's name does not shadow the instance."""
    state = [
        _resource('openstack_compute_instance_v2', 'stack-tapm-0', 'instance-id'),
        _resource('openstack_taas_tap_mirror_v2', 'stack-tapm-0', 'tap-mirror-id'),
    ]
    with patch.object(manager, 'list_stack_resources', return_value=state):
        assert manager.get_resource_id('stack', 'tapm-0') == 'instance-id'


def test_get_resource_id_finds_aws_instance(manager) -> None:
    """AWS instances are resolved the same way as OpenStack ones."""
    state = [
        _resource('aws_instance', 'stack-server', 'i-0abc'),
        _resource('aws_network_interface', 'stack-server', 'eni-1'),
    ]
    with patch.object(manager, 'list_stack_resources', return_value=state):
        assert manager.get_resource_id('stack', 'server') == 'i-0abc'


def _port(type_: str, name: str, mac: str, ip: str) -> dict:
    return {
        'mode': 'managed',
        'type': type_,
        'name': name,
        'instances': [{'attributes': {'mac_address': mac, 'ip': ip}}],
    }


@pytest.mark.parametrize(
    ('port_type', 'instance_type', 'association_type'),
    [
        (
            'openstack_networking_port_v2',
            'openstack_compute_instance_v2',
            'openstack_networking_floatingip_associate_v2',
        ),
        ('aws_network_interface', 'aws_instance', 'aws_eip_association'),
    ],
)
def test_get_enriched_topology_instance_ignores_non_port_with_port_name(
    manager, port_type, instance_type, association_type
) -> None:
    """Port attributes come from the port even when a later resource shares its name."""
    manager.trc.man_out_port = 'man-out'
    manager.cloud_client.get_private_ip.side_effect = lambda attrs: attrs['ip']
    link = MagicMock()
    link.name = 'link-1'
    topology = MagicMock()
    topology.get_links.return_value = [link]
    state = [
        _port(port_type, 'stack-man-out', 'mac-man', '10.0.0.1'),
        _port(port_type, 'stack-link-1', 'mac-1', '10.0.0.2'),
        _resource(instance_type, 'stack-man-out', 'instance-id'),
        _resource(association_type, 'stack-link-1', 'assoc-id'),
    ]
    with patch.object(manager, 'list_stack_resources', return_value=state):
        result = manager.get_enriched_topology_instance('stack', topology)
    assert result.ip == '10.0.0.1'
    assert link.ip == '10.0.0.2'
    assert link.mac == 'mac-1'
