"""Tests for TopologyInstance link resolution."""

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


def _trc() -> TransformationConfiguration:
    return TransformationConfiguration(
        man_image='man-image', man_flavor='man-flavor', man_user='debian'
    )


def _main_link(instance: TopologyInstance, node_name: str) -> tuple[str, str | None] | None:
    node = instance.get_node(node_name)
    assert node is not None
    link = instance.get_node_main_link(node)
    return None if link is None else (link.network.name, link.ip)


def test_get_node_main_link_is_first_net_mapping() -> None:
    """A multi-homed host's main link is its first net_mapping, not its first network."""
    reversed_networks = _BASE_TOPOLOGY.replace(
        """  - name: server-switch
    cidr: 10.10.20.0/24
  - name: monitoring-switch
    cidr: 10.10.40.0/24
""",
        """  - name: monitoring-switch
    cidr: 10.10.40.0/24
  - name: server-switch
    cidr: 10.10.20.0/24
""",
    )
    instance = TopologyInstance(TopologyDefinition.load(reversed_networks), _trc())

    assert [network.name for network in instance.get_hosts_networks()] == [
        'monitoring-switch',
        'server-switch',
    ]
    assert _main_link(instance, 'monitoring') == ('server-switch', '10.10.20.6')


def test_get_node_main_link_single_homed_host() -> None:
    """A single-homed host's main link is its only user-network link."""
    instance = TopologyInstance(TopologyDefinition.load(_BASE_TOPOLOGY), _trc())

    assert _main_link(instance, 'server') == ('server-switch', '10.10.20.5')


def test_get_node_main_link_router_is_first_router_mapping() -> None:
    """A router's main link is its first router_mapping, never its WAN link."""
    reversed_mappings = _BASE_TOPOLOGY.replace(
        """  - router: server-router
    network: server-switch
    ip: 10.10.20.1
  - router: server-router
    network: monitoring-switch
    ip: 10.10.40.1
""",
        """  - router: server-router
    network: monitoring-switch
    ip: 10.10.40.1
  - router: server-router
    network: server-switch
    ip: 10.10.20.1
""",
    )
    instance = TopologyInstance(TopologyDefinition.load(_BASE_TOPOLOGY), _trc())
    reversed_instance = TopologyInstance(TopologyDefinition.load(reversed_mappings), _trc())

    assert _main_link(instance, 'server-router') == ('server-switch', '10.10.20.1')
    assert _main_link(reversed_instance, 'server-router') == ('monitoring-switch', '10.10.40.1')


def test_get_node_main_link_none_for_man() -> None:
    """MAN has only management and WAN links, so it has no main link."""
    instance = TopologyInstance(TopologyDefinition.load(_BASE_TOPOLOGY), _trc())

    assert _main_link(instance, 'man') is None
