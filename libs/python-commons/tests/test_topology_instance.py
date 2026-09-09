"""Tests for TopologyInstance network-forwarding (port mirroring) resolution."""

from crczp.topology_definition.models import TopologyDefinition

from crczp.cloud_commons.topology_instance import TopologyInstance
from crczp.cloud_commons.transformation_configuration import TransformationConfiguration

_BASE_TOPOLOGY = """
name: small-sandbox
hosts:
  - name: server
    base_box: { image: crczp/debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small
  - name: monitoring
    base_box: { image: crczp/debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small
routers:
  - name: server-router
    base_box: { image: crczp/debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small
wan:
  name: internet-connection
  cidr: 100.100.100.0/29
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
    - { host: server-router, network: server-switch }
  destination: { host: monitoring, network: monitoring-switch }
  direction: both
"""


def _trc() -> TransformationConfiguration:
    return TransformationConfiguration(
        man_image='man-image', man_flavor='man-flavor', man_user='debian'
    )


def test_get_network_forwarding_resolves_links() -> None:
    """A network_forwarding rule is resolved into concrete source/destination Links."""
    topology = TopologyDefinition.load(_BASE_TOPOLOGY + _FORWARDING)
    instance = TopologyInstance(topology, _trc())

    rule = instance.get_network_forwarding()

    assert rule is not None
    assert rule.direction == 'both'
    assert len(rule.sources) == 2
    assert {(link.node.name, link.network.name) for link in rule.sources} == {
        ('server', 'server-switch'),
        ('server-router', 'server-switch'),
    }
    assert rule.destination.node.name == 'monitoring'
    assert rule.destination.network.name == 'monitoring-switch'


def test_get_network_forwarding_none_when_absent() -> None:
    """A topology without network_forwarding resolves to None."""
    topology = TopologyDefinition.load(_BASE_TOPOLOGY)
    instance = TopologyInstance(topology, _trc())

    assert instance.get_network_forwarding() is None
