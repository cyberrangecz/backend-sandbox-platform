"""
Tests for topology definition serialization.
"""

import io
import os
from typing import Any, Optional

import pytest
from ruamel.yaml import YAML
from yamlize.yamlizing_error import YamlizingError

from crczp.topology_definition.image_naming import image_name_replace, image_name_strip
from crczp.topology_definition.models import (
    BaseBox,
    Host,
    Protocol,
    Router,
    TopologyDefinition,
)

SANDBOX_DEFINITION_PATH = os.path.join(os.path.dirname(__file__), 'assets/topology.yml')
SANDBOX_DEFINITION_MONITORING_PATH = os.path.join(
    os.path.dirname(__file__), 'assets/topology-with-monitoring.yml'
)
SANDBOX_DEFINITION_VPN_PATH = os.path.join(
    os.path.dirname(__file__), 'assets/topology-with-vpn.yml'
)


@pytest.fixture  # type: ignore[untyped-decorator]
def topology_definition_string() -> str:
    """
    Fixture for topology definition string.
    """
    with open(SANDBOX_DEFINITION_PATH, encoding='utf-8') as f:
        return f.read()


@pytest.fixture  # type: ignore[untyped-decorator]
def topology_definition_dict() -> dict[str, Any]:
    """
    Fixture for topology definition dict.
    """
    with open(SANDBOX_DEFINITION_PATH, encoding='utf-8') as f:
        return dict(YAML(typ='safe', pure=True).load(f))


@pytest.fixture  # type: ignore[untyped-decorator]
def topology_definition() -> TopologyDefinition:
    """
    Fixture for topology definition.
    """
    return TopologyDefinition.from_file(SANDBOX_DEFINITION_PATH)


@pytest.fixture  # type: ignore[untyped-decorator]
def topology_definition_monitoring() -> TopologyDefinition:
    """
    Fixture for topology definition with monitoring.
    """
    return TopologyDefinition.from_file(SANDBOX_DEFINITION_MONITORING_PATH)


@pytest.mark.integration
class TestDummy:
    """
    Test class for topology definition.
    """

    def test_read_yaml(self, topology_definition: TopologyDefinition) -> None:
        """
        Test reading YAML.
        """
        assert topology_definition is not None
        assert len(topology_definition.hosts) == 2
        network = topology_definition.find_network_by_name('home-switch')
        assert network is not None
        assert not network.accessible_by_user
        server = topology_definition.find_host_by_name('server')
        assert server is not None
        assert server.base_box.mgmt_protocol == Protocol.SSH
        assert server.extra is None
        home = topology_definition.find_host_by_name('home')
        assert home is not None
        assert home.base_box.mgmt_protocol == Protocol.WINRM
        assert home.extra is not None
        assert home.extra['hello'] == 'yello'
        assert home.extra['yello'] == 5
        assert home.extra['foo']

    def test_read_yaml_monitoring(self, topology_definition_monitoring: TopologyDefinition) -> None:
        """
        Test reading YAML with monitoring.
        """
        assert topology_definition_monitoring is not None
        assert len(topology_definition_monitoring.hosts) == 2
        network = topology_definition_monitoring.find_network_by_name('home-switch')
        assert network is not None
        assert not network.accessible_by_user

        # TCP targets
        assert topology_definition_monitoring.monitoring_targets is not None
        assert len(topology_definition_monitoring.monitoring_targets.tcp) == 1
        router_tcp = topology_definition_monitoring.monitoring_targets.tcp[0]
        assert router_tcp.node == 'server-router'
        assert len(router_tcp.targets) == 2
        assert router_tcp.targets[0].port == 22
        assert router_tcp.targets[0].interface == 'ens3'
        assert router_tcp.targets[1].port == 22
        assert router_tcp.targets[1].address == '10.10.20.0/24'

        # ICMP targets
        assert len(topology_definition_monitoring.monitoring_targets.icmp) == 2
        server_icmp = topology_definition_monitoring.monitoring_targets.icmp[0]
        assert server_icmp.node == 'server'
        assert server_icmp.targets[0].interface == 'ens3'
        home_icmp = topology_definition_monitoring.monitoring_targets.icmp[1]
        assert home_icmp.node == 'home'
        assert home_icmp.targets[0].address == '10.10.30.5'

        # HTTP targets
        assert topology_definition_monitoring.monitoring_targets.http is not None
        http_targets = topology_definition_monitoring.monitoring_targets.http.targets
        assert len(http_targets) == 1
        assert http_targets[0].url == 'https://10.10.20.5'
        assert http_targets[0].check_string == 'Hello'

    def test_read_yaml_monitoring_with_only_http(
        self, topology_definition_string: str
    ) -> None:
        """
        Test reading YAML with only HTTP monitoring targets configured.
        """
        topology_definition_with_http_only = (
            topology_definition_string
            + """
monitoring_targets:
  http:
    targets:
      - url: https://10.10.20.5
        check_string: Hello
"""
        )

        topology_definition = TopologyDefinition.load(topology_definition_with_http_only)

        assert topology_definition.monitoring_targets is not None
        assert topology_definition.monitoring_targets.http is not None
        http_targets = topology_definition.monitoring_targets.http.targets
        assert len(http_targets) == 1
        assert http_targets[0].url == 'https://10.10.20.5'
        assert http_targets[0].check_string == 'Hello'
        assert topology_definition.monitoring_targets.tcp in (None, [])
        assert topology_definition.monitoring_targets.icmp in (None, [])

    def test_indexes(self, topology_definition: TopologyDefinition) -> None:
        """
        Test indexes.
        """
        assert topology_definition.find_host_by_name('server') is not None
        assert topology_definition.find_host_by_name('home') is not None

    def test_read_yaml_bad_protocol(self, topology_definition_string: str) -> None:
        """
        Test reading YAML with bad protocol.
        """
        bad_topology_definition_string = topology_definition_string.replace(
            'winrm', 'InvalidProtocol'
        )

        with pytest.raises(ValueError):
            TopologyDefinition.load(bad_topology_definition_string)

    def test_cidr_overlaps(self) -> None:
        """
        Test CIDR overlaps.
        """
        with open(SANDBOX_DEFINITION_PATH, encoding='utf-8') as f:
            sb_def = f.read().replace('cidr: 100.100.100.0/29', 'cidr: 10.10.20.0/29')

        with pytest.raises(YamlizingError):
            TopologyDefinition.load(sb_def)

    def test_ip_not_in_network(self) -> None:
        """
        Test IP not in network.
        """
        with open(SANDBOX_DEFINITION_PATH, encoding='utf-8') as f:
            sb_def = f.read().replace('ip: 10.10.20.5', 'ip: 10.10.40.5')

        with pytest.raises(YamlizingError):
            TopologyDefinition.load(sb_def)

    def test_ip_not_unique(self) -> None:
        """
        Test IP not unique.
        """
        with open(SANDBOX_DEFINITION_PATH, encoding='utf-8') as f:
            sb_def = f.read().replace('ip: 10.10.20.5', 'ip: 10.10.20.1')

        with pytest.raises(YamlizingError):
            TopologyDefinition.load(sb_def)

    def test_multi_protocol_base_box(self, topology_definition_dict: dict[str, Any]) -> None:
        """
        Test multi protocol base box.
        """
        server_base_box_dict = topology_definition_dict['hosts'][0]['base_box']
        server_base_box_dict['mng_protocol'] = 'ssh'
        server_base_box_dict['mgmt_protocol'] = 'ssh'

        with pytest.raises(YamlizingError):
            output_stream = io.StringIO()
            YAML(typ='full').dump(server_base_box_dict, output_stream)
            BaseBox.load(output_stream.getvalue())

    def test_multi_user_base_box(self, topology_definition_dict: dict[str, Any]) -> None:
        """
        Test multi user base box.
        """
        server_base_box_dict = topology_definition_dict['hosts'][0]['base_box']
        server_base_box_dict['man_user'] = 'debian'
        server_base_box_dict['mgmt_user'] = 'debian'

        with pytest.raises(YamlizingError):
            output_stream = io.StringIO()
            YAML(typ='full').dump(server_base_box_dict, output_stream)
            BaseBox.load(output_stream.getvalue())

    def test_deprecated_base_box_attributes(self, topology_definition_dict: dict[str, Any]) -> None:
        """
        Test deprecated base box attributes.
        """
        server_router_base_box_dict = topology_definition_dict['routers'][0]['base_box']
        output_stream = io.StringIO()
        YAML(typ='full').dump(server_router_base_box_dict, output_stream)
        server_router_base_box = BaseBox.load(output_stream.getvalue())

        assert not hasattr(server_router_base_box, 'man_user')
        assert not hasattr(server_router_base_box, 'mng_protocol')
        assert server_router_base_box.mgmt_user
        assert server_router_base_box.mgmt_protocol

    def test_image_name_replace_1(self, topology_definition: TopologyDefinition) -> None:
        """
        Test image name replace 1.
        """
        td = image_name_replace('w', 'X', topology_definition)

        home: Optional[Host] = td.find_host_by_name('home')
        assert home is not None
        assert home.base_box.image == 'Xindows/windows-10-amd64'

        home_router: Optional[Router] = td.find_router_by_name('home-router')
        assert home_router is not None
        assert home_router.base_box.image == 'debian/debian-12-x86_64'

    def test_image_name_replace_2(self, topology_definition: TopologyDefinition) -> None:
        """
        Test image name replace 2.
        """
        td = image_name_replace(r'.*/', 'crczp-', topology_definition)

        home: Optional[Host] = td.find_host_by_name('home')
        assert home is not None
        assert home.base_box.image == 'crczp-windows-10-amd64'

        home_router: Optional[Router] = td.find_router_by_name('home-router')
        assert home_router is not None
        assert home_router.base_box.image == 'crczp-debian-12-x86_64'

    def test_vpn_absent(self, topology_definition: TopologyDefinition) -> None:
        """
        Topology without a vpn block loads without error; attribute is None.
        """
        assert topology_definition.vpn is None

    def test_vpn_empty_entrypoints_list(self, topology_definition_string: str) -> None:
        """
        vpn.entrypoints: [] is valid — an empty list is not an error.
        """
        td = TopologyDefinition.load(
            topology_definition_string + '\nvpn:\n  entrypoints: []\n'
        )
        assert td.vpn is not None
        assert td.vpn.entrypoints is not None
        assert len(td.vpn.entrypoints) == 0

    def test_vpn_entrypoints_loaded(self, topology_definition_string: str) -> None:
        """
        Topology with vpn.entrypoints is parsed correctly.
        """
        td = TopologyDefinition.load(
            topology_definition_string
            + """
vpn:
  entrypoints:
    - name: server
      routes:
        - 10.10.0.0/16
        - 192.168.100.0/24
"""
        )
        assert td.vpn is not None
        assert td.vpn.entrypoints is not None
        assert len(td.vpn.entrypoints) == 1
        ep = td.vpn.entrypoints[0]
        assert ep.name == 'server'
        assert list(ep.routes) == ['10.10.0.0/16', '192.168.100.0/24']

    def test_vpn_entrypoints_multiple(self, topology_definition_string: str) -> None:
        """
        Multiple vpn.entrypoints are all parsed.
        """
        td = TopologyDefinition.load(
            topology_definition_string
            + """
vpn:
  entrypoints:
    - name: server
      routes:
        - 10.10.0.0/16
    - name: home
      routes:
        - 172.16.0.0/12
"""
        )
        assert td.vpn is not None
        assert td.vpn.entrypoints is not None
        assert len(td.vpn.entrypoints) == 2
        assert td.vpn.entrypoints[0].name == 'server'
        assert td.vpn.entrypoints[1].name == 'home'

    def test_vpn_from_file(self) -> None:
        """
        Topology loaded from file with a vpn block parses entrypoints and DNS.
        """
        td = TopologyDefinition.from_file(SANDBOX_DEFINITION_VPN_PATH)
        assert td.vpn is not None
        assert td.vpn.entrypoints is not None
        assert len(td.vpn.entrypoints) == 1
        ep = td.vpn.entrypoints[0]
        assert ep.name == 'vpn-gw'
        assert '10.10.0.0/16' in list(ep.routes)
        assert '192.168.100.0/24' in list(ep.routes)
        assert td.vpn.dns is not None
        assert list(td.vpn.dns.servers) == ['10.10.20.5']
        assert list(td.vpn.dns.search_domains) == ['sandbox.local']

    def test_vpn_entrypoint_router_loaded(self, topology_definition_string: str) -> None:
        """
        VpnEntrypoint whose name references a router (not a host) is accepted.
        """
        td = TopologyDefinition.load(
            topology_definition_string
            + """
vpn:
  entrypoints:
    - name: server-router
      routes:
        - 10.10.0.0/16
"""
        )
        assert td.vpn is not None
        assert td.vpn.entrypoints is not None
        assert len(td.vpn.entrypoints) == 1
        assert td.vpn.entrypoints[0].name == 'server-router'

    def test_vpn_entrypoint_unknown_node_rejected(self, topology_definition_string: str) -> None:
        """
        VpnEntrypoint whose name does not match any host or router is rejected at parse time.
        """
        with pytest.raises((YamlizingError, ValueError)):
            TopologyDefinition.load(
                topology_definition_string
                + """
vpn:
  entrypoints:
    - name: nonexistent-node
      routes:
        - 10.10.0.0/16
"""
            )

    def test_vpn_entrypoint_empty_name_rejected(self, topology_definition_string: str) -> None:
        """
        VpnEntrypoint with empty name raises a parse-time error.
        """
        with pytest.raises((YamlizingError, ValueError)):
            TopologyDefinition.load(
                topology_definition_string
                + """
vpn:
  entrypoints:
    - name: ''
      routes:
        - 10.10.0.0/16
"""
            )

    def test_vpn_entrypoint_empty_routes_rejected(self, topology_definition_string: str) -> None:
        """
        VpnEntrypoint with empty routes list raises a parse-time error.
        """
        with pytest.raises((YamlizingError, ValueError)):
            TopologyDefinition.load(
                topology_definition_string
                + """
vpn:
  entrypoints:
    - name: server
      routes: []
"""
            )

    def test_vpn_entrypoint_invalid_cidr_rejected(self, topology_definition_string: str) -> None:
        """
        VpnEntrypoint with an invalid CIDR in routes raises a parse-time error.
        """
        with pytest.raises((YamlizingError, ValueError)):
            TopologyDefinition.load(
                topology_definition_string
                + """
vpn:
  entrypoints:
    - name: server
      routes:
        - not-a-cidr
"""
            )

    def test_vpn_entrypoint_ipv6_cidr_rejected(self, topology_definition_string: str) -> None:
        """
        VpnEntrypoint with an IPv6 CIDR is rejected — only IPv4 is supported.
        """
        with pytest.raises((YamlizingError, ValueError)):
            TopologyDefinition.load(
                topology_definition_string
                + """
vpn:
  entrypoints:
    - name: server
      routes:
        - fd00::/8
"""
            )

    def test_vpn_dns_loaded(self, topology_definition_string: str) -> None:
        """
        vpn.dns with servers and search_domains is parsed correctly.
        """
        td = TopologyDefinition.load(
            topology_definition_string
            + """
vpn:
  dns:
    servers:
      - 10.10.20.5
      - 8.8.8.8
    search_domains:
      - sandbox.local
      - example.com
"""
        )
        assert td.vpn is not None
        assert td.vpn.dns is not None
        assert list(td.vpn.dns.servers) == ['10.10.20.5', '8.8.8.8']
        assert list(td.vpn.dns.search_domains) == ['sandbox.local', 'example.com']

    def test_vpn_dns_servers_only(self, topology_definition_string: str) -> None:
        """
        vpn.dns without search_domains is valid; search_domains defaults to None.
        """
        td = TopologyDefinition.load(
            topology_definition_string
            + """
vpn:
  dns:
    servers:
      - 10.10.20.5
"""
        )
        assert td.vpn is not None
        assert td.vpn.dns is not None
        assert list(td.vpn.dns.servers) == ['10.10.20.5']
        assert td.vpn.dns.search_domains is None

    def test_vpn_dns_absent(self, topology_definition_string: str) -> None:
        """
        A vpn block with only entrypoints leaves dns as None.
        """
        td = TopologyDefinition.load(
            topology_definition_string
            + """
vpn:
  entrypoints:
    - name: server
      routes:
        - 10.10.0.0/16
"""
        )
        assert td.vpn is not None
        assert td.vpn.dns is None

    def test_vpn_dns_empty_servers_rejected(self, topology_definition_string: str) -> None:
        """
        vpn.dns.servers: [] raises a parse-time error.
        """
        with pytest.raises((YamlizingError, ValueError)):
            TopologyDefinition.load(
                topology_definition_string
                + """
vpn:
  dns:
    servers: []
"""
            )

    def test_vpn_dns_missing_servers_rejected(self, topology_definition_string: str) -> None:
        """
        vpn.dns with no servers (only search_domains) raises a parse-time error.
        """
        with pytest.raises((YamlizingError, ValueError)):
            TopologyDefinition.load(
                topology_definition_string
                + """
vpn:
  dns:
    search_domains:
      - sandbox.local
"""
            )

    def test_vpn_dns_invalid_server_rejected(self, topology_definition_string: str) -> None:
        """
        vpn.dns.servers with a non-IP value raises a parse-time error.
        """
        with pytest.raises((YamlizingError, ValueError)):
            TopologyDefinition.load(
                topology_definition_string
                + """
vpn:
  dns:
    servers:
      - not-an-ip
"""
            )

    def test_vpn_dns_ipv6_server_rejected(self, topology_definition_string: str) -> None:
        """
        vpn.dns.servers with an IPv6 address is rejected — only IPv4 is supported.
        """
        with pytest.raises((YamlizingError, ValueError)):
            TopologyDefinition.load(
                topology_definition_string
                + """
vpn:
  dns:
    servers:
      - fd00::1
"""
            )

    def test_vpn_dns_invalid_search_domain_rejected(self, topology_definition_string: str) -> None:
        """
        vpn.dns.search_domains with an invalid domain raises a parse-time error.
        """
        with pytest.raises((YamlizingError, ValueError)):
            TopologyDefinition.load(
                topology_definition_string
                + """
vpn:
  dns:
    servers:
      - 10.10.20.5
    search_domains:
      - 'not a domain'
"""
            )

    def test_image_name_strip(self, topology_definition: TopologyDefinition) -> None:
        """
        Test image name strip.
        """
        td = image_name_strip('crczp/', topology_definition)

        home: Optional[Host] = td.find_host_by_name('home')
        assert home is not None
        assert home.base_box.image == 'windows/windows-10-amd64'

        server_router: Optional[Router] = td.find_router_by_name('server-router')
        assert server_router is not None
        assert server_router.base_box.image == 'debian-12-x86_64'
