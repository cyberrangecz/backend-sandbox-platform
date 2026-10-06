"""Tests for the default route each AWS instance sets in its user data."""

from collections.abc import Callable

from crczp.aws_driver.aws_client import CrczpAwsClient, get_default_route_ip
from crczp.cloud_commons import TopologyInstance

_PREFIX = 'stack-p1-s2'

_UNMAPPED_HOST_TOPOLOGY = """
name: unmapped-host
hosts:
  - name: server
    base_box: { image: ami-0abc, mgmt_user: debian }
    flavor: t3.small
  - name: isolated
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
  - { host: server, network: server-switch, ip: 10.10.20.5 }
router_mappings:
  - { router: server-router, network: server-switch, ip: 10.10.20.1 }
groups: []
"""


def _default_route_ip(instance: TopologyInstance, node_name: str) -> str | None:
    node = instance.get_node(node_name)
    assert node is not None
    return get_default_route_ip(instance, node)


def test_default_route_follows_main_link(topology: Callable[..., TopologyInstance]) -> None:
    """The gateway is that of the first mapping, not of the first network in networks:."""
    instance = topology()

    assert _default_route_ip(instance, 'monitoring') == '10.10.40.1'
    assert _default_route_ip(instance, 'server-router') == '10.10.20.1'


def test_node_without_user_network_has_no_default_route(
    aws_client: CrczpAwsClient,
    topology_instance_from: Callable[[str], TopologyInstance],
    resource_block: Callable[[str, str, str], str],
) -> None:
    """A host without a net_mapping keeps the default route its image sets up."""
    instance = topology_instance_from(_UNMAPPED_HOST_TOPOLOGY)

    template = aws_client.create_terraform_template(instance, resource_prefix=_PREFIX)

    assert _default_route_ip(instance, 'isolated') is None
    assert 'ip r ' not in resource_block(template, 'aws_instance', f'{_PREFIX}-isolated')
    assert 'ip r add default via 10.10.20.1' in resource_block(
        template, 'aws_instance', f'{_PREFIX}-server'
    )
