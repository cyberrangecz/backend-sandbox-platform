"""
Tests for topology definition serialization.
"""

import io
import os
from typing import Any

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
SANDBOX_DEFINITION_VOLUMES_PATH = os.path.join(
    os.path.dirname(__file__), 'assets/topology-with-volumes.yml'
)


@pytest.fixture(name='topology_definition_string')
def fixture_topology_definition_string() -> str:
    """
    Fixture for topology definition string.
    """
    with open(SANDBOX_DEFINITION_PATH, encoding='utf-8') as f:
        return f.read()


@pytest.fixture(name='topology_definition_dict')
def fixture_topology_definition_dict() -> dict[str, Any]:
    """
    Fixture for topology definition dict.
    """
    with open(SANDBOX_DEFINITION_PATH, encoding='utf-8') as f:
        return dict(YAML(typ='safe', pure=True).load(f))


@pytest.fixture(name='topology_definition')
def fixture_topology_definition() -> TopologyDefinition:
    """
    Fixture for topology definition.
    """
    return TopologyDefinition.from_file(SANDBOX_DEFINITION_PATH)


@pytest.fixture(name='topology_definition_monitoring')
def fixture_topology_definition_monitoring() -> TopologyDefinition:
    """
    Fixture for topology definition with monitoring.
    """
    return TopologyDefinition.from_file(SANDBOX_DEFINITION_MONITORING_PATH)


@pytest.fixture(name='topology_definition_volumes')
def fixture_topology_definition_volumes() -> TopologyDefinition:
    """
    Fixture for topology definition whose 'server' host declares extra volumes.
    """
    return TopologyDefinition.from_file(SANDBOX_DEFINITION_VOLUMES_PATH)


@pytest.mark.integration
class TestDummy:  # pylint: disable=too-many-public-methods
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

    def test_read_yaml_monitoring_with_only_http(self, topology_definition_string: str) -> None:
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
            YAML(typ='safe').dump(server_base_box_dict, output_stream)
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
            YAML(typ='safe').dump(server_base_box_dict, output_stream)
            BaseBox.load(output_stream.getvalue())

    def test_deprecated_base_box_attributes(self, topology_definition_dict: dict[str, Any]) -> None:
        """
        Test deprecated base box attributes.
        """
        server_router_base_box_dict = topology_definition_dict['routers'][0]['base_box']
        output_stream = io.StringIO()
        YAML(typ='safe').dump(server_router_base_box_dict, output_stream)
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

        home: Host | None = td.find_host_by_name('home')
        assert home is not None
        assert home.base_box.image == 'Xindows/windows-10-amd64'

        home_router: Router | None = td.find_router_by_name('home-router')
        assert home_router is not None
        assert home_router.base_box.image == 'debian/debian-12-x86_64'

    def test_image_name_replace_2(self, topology_definition: TopologyDefinition) -> None:
        """
        Test image name replace 2.
        """
        td = image_name_replace(r'.*/', 'crczp-', topology_definition)

        home: Host | None = td.find_host_by_name('home')
        assert home is not None
        assert home.base_box.image == 'crczp-windows-10-amd64'

        home_router: Router | None = td.find_router_by_name('home-router')
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
        td = TopologyDefinition.load(topology_definition_string + '\nvpn:\n  entrypoints: []\n')
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

        home: Host | None = td.find_host_by_name('home')
        assert home is not None
        assert home.base_box.image == 'windows/windows-10-amd64'

        server_router: Router | None = td.find_router_by_name('server-router')
        assert server_router is not None
        assert server_router.base_box.image == 'debian-12-x86_64'


class TestVolumeRules:
    """
    Tests for the volume schema rules.
    """

    @staticmethod
    def _load(topology_definition_string: str, volumes: str) -> TopologyDefinition:
        host_line = '  - name: home\n'
        assert host_line in topology_definition_string
        return TopologyDefinition.load(
            topology_definition_string.replace(host_line, host_line + '    volumes:\n' + volumes, 1)
        )

    @pytest.mark.parametrize(
        ('volumes', 'message'),
        [
            ('      - image: snap-0123\n', r"attributes without default: \['size'\]"),
            ('      - size: 0\n', r'volumes\[0\]\.size must be a whole number of at least 1'),
            (
                '      - size: 10\n      - size: -5\n',
                r'volumes\[1\]\.size must be a whole number of at least 1',
            ),
            ('      - size: true\n', r'volumes\[0\]\.size must be a whole number of at least 1'),
            ('      - size: 10\n        image: crczp/disk\n', r'volumes\[0\] is the system disk'),
            ('      - size: 10\n' * 13, 'more than 12 entries'),
        ],
        ids=[
            'no-size',
            'size-0',
            'size-negative',
            'size-bool',
            'image-on-system-disk',
            '13-volumes',
        ],
    )
    def test_invalid_volumes_rejected(
        self, topology_definition_string: str, volumes: str, message: str
    ) -> None:
        """
        Volumes without size, below 1 GB, with an image on the system disk or over 12 entries fail.
        """
        with pytest.raises(YamlizingError, match=message):
            self._load(topology_definition_string, volumes)

    def test_twelve_volumes_accepted(self, topology_definition_string: str) -> None:
        """
        Exactly 12 volumes are allowed.
        """
        td = self._load(topology_definition_string, '      - size: 10\n' * 12)
        host = td.find_host_by_name('home')
        assert host is not None
        assert host.volumes is not None
        assert len(host.volumes) == 12

    def test_image_on_extra_volumes_accepted(self, topology_definition_string: str) -> None:
        """
        Only the extra volumes may be created from an image.
        """
        td = self._load(
            topology_definition_string,
            '      - size: 10\n      - size: 20\n        image: crczp/disk\n',
        )
        host = td.find_host_by_name('home')
        assert host is not None
        assert host.volumes is not None
        assert [volume.image for volume in host.volumes] == [None, 'crczp/disk']

    def test_volume_image_loaded(self, topology_definition_volumes: TopologyDefinition) -> None:
        """
        A volume may optionally declare its own base image; volumes without one keep image=None.
        """
        server: Host | None = topology_definition_volumes.find_host_by_name('server')
        assert server is not None
        assert server.volumes is not None
        assert [volume.size for volume in server.volumes] == [20, 30, 40]
        assert [volume.image for volume in server.volumes] == [
            None,
            'crczp/data-disk-x86_64',
            None,
        ]

    def test_image_name_replace_rewrites_volume_images(
        self, topology_definition_volumes: TopologyDefinition
    ) -> None:
        """
        The image-naming strategy rewrites per-volume images like it does base_box images,
        while leaving volumes without an image untouched.
        """
        td = image_name_replace(r'.*/', 'crczp-', topology_definition_volumes)

        server: Host | None = td.find_host_by_name('server')
        assert server is not None
        assert server.base_box.image == 'crczp-debian-12-x86_64'
        assert server.volumes is not None
        assert [volume.image for volume in server.volumes] == [
            None,
            'crczp-data-disk-x86_64',
            None,
        ]


class TestManagementAccess:
    """
    Tests for the BaseBox management access settings.
    """

    def test_mgmt_password_and_managed_defaults(
        self, topology_definition: TopologyDefinition
    ) -> None:
        """
        mgmt_password is optional and defaults to None; managed defaults to True.
        """
        server = topology_definition.find_host_by_name('server')
        assert server is not None
        assert server.base_box.mgmt_password is None
        assert server.managed is True

    def test_mgmt_password_and_unmanaged_load(self) -> None:
        """
        A host may declare an SSH password and opt out of the platform's networking stage.
        """
        host = Host.load(
            'name: appliance\n'
            'base_box:\n'
            '  image: appliance-image\n'
            '  mgmt_user: admin\n'
            '  mgmt_password: test-password\n'
            'flavor: standard.large\n'
            'managed: false\n'
        )
        assert host.managed is False
        assert host.base_box.mgmt_user == 'admin'
        assert host.base_box.mgmt_password == 'test-password'

    @pytest.mark.parametrize(('raw', 'expected'), [('1234', '1234'), ('0123', '0123')])
    def test_mgmt_password_unquoted_number_loads_as_string(self, raw: str, expected: str) -> None:
        """
        An unquoted numeric mgmt_password loads as the literal string.
        """
        base_box = BaseBox.load(f'image: image\nmgmt_password: {raw}\n')
        assert base_box.mgmt_password == expected

    @pytest.mark.parametrize('raw', ['', '~', 'null'])
    def test_mgmt_password_null_spellings_mean_no_password(self, raw: str) -> None:
        """
        Empty, ~ and null mgmt_password stay None and are accepted with a non-SSH protocol.
        """
        base_box = BaseBox.load(f'image: image\nmgmt_protocol: winrm\nmgmt_password: {raw}\n')
        assert base_box.mgmt_password is None

    @pytest.mark.parametrize(
        'base_box_yaml',
        [
            'image: image\nmgmt_protocol: winrm\nmgmt_password: test-password\n',
            'image: image\nmgmt_password: test-password\nmgmt_protocol: winrm\n',
        ],
    )
    def test_mgmt_password_rejected_without_ssh(self, base_box_yaml: str) -> None:
        """
        mgmt_password is rejected with a non-SSH protocol regardless of key order.
        """
        with pytest.raises(YamlizingError, match='mgmt_password is supported only with'):
            BaseBox.load(base_box_yaml)

    def test_router_mgmt_password_rejected_without_ssh(
        self, topology_definition_string: str
    ) -> None:
        """
        Routers share BaseBox, so a router with winrm and a password is rejected too.
        """
        home_router_base_box = 'base_box: { image: debian/debian-12-x86_64 }'
        assert home_router_base_box in topology_definition_string
        sb_def = topology_definition_string.replace(
            home_router_base_box,
            'base_box: { image: debian/debian-12-x86_64, mgmt_protocol: winrm,'
            ' mgmt_password: test-password }',
        )

        with pytest.raises(YamlizingError, match='mgmt_password is supported only with'):
            TopologyDefinition.load(sb_def)

    def test_empty_mgmt_password_rejected(self) -> None:
        """
        An empty mgmt_password is rejected at its key rather than ignored by the inventory.
        """
        with pytest.raises(YamlizingError, match='mgmt_password must not be empty') as exc_info:
            BaseBox.load("image: image\nmgmt_password: ''\n")
        assert 'line 2, column' in str(exc_info.value)

    def test_mgmt_password_anchor_keeps_type_elsewhere(self) -> None:
        """
        Loading a numeric mgmt_password as a string leaves other uses of its anchor untouched.
        """
        host = Host.load(
            'name: appliance\n'
            'base_box:\n'
            '  image: appliance-image\n'
            '  mgmt_password: &size 20\n'
            'flavor: standard.large\n'
            'volumes:\n'
            '  - size: *size\n'
        )
        assert host.base_box.mgmt_password == '20'
        assert host.volumes is not None
        assert isinstance(host.volumes[0].size, int)
        assert host.volumes[0].size == 20


class TestUnmanagedHosts:
    """
    Tests for hosts with managed: false.
    """

    @pytest.mark.parametrize(
        ('block', 'error'),
        [
            (
                'monitoring_targets:\n  tcp:\n    - node: server\n      targets:\n'
                '        - port: 22\n          interface: ens3\n',
                'never gathers its facts.*TCP',
            ),
            (
                'monitoring_targets:\n  icmp:\n    - node: server\n      targets:\n'
                '        - interface: ens3\n',
                'never gathers its facts.*ICMP',
            ),
            (
                'vpn:\n  entrypoints:\n    - name: server\n      routes:\n        - 10.10.0.0/16\n',
                'never installs the NetBird agent',
            ),
        ],
        ids=['tcp', 'icmp', 'vpn'],
    )
    def test_unmanaged_host_rejected_where_stage_one_is_needed(
        self, topology_definition_string: str, block: str, error: str
    ) -> None:
        """
        An unmanaged host cannot be a monitoring target or a VPN entrypoint; a managed one can.
        """
        TopologyDefinition.load(topology_definition_string + '\n' + block)

        server_flavor = '    flavor: standard.small\n    block_internet: True\n'
        assert server_flavor in topology_definition_string
        unmanaged = topology_definition_string.replace(
            server_flavor, server_flavor + '    managed: false\n', 1
        )
        with pytest.raises(YamlizingError, match=error):
            TopologyDefinition.load(unmanaged + '\n' + block)

    def test_router_managed_rejected(self, topology_definition_string: str) -> None:
        """
        managed is Host-only; a router carrying it is a schema error.
        """
        router_flavor = '    flavor: standard.small\n\n  - name: home-router'
        assert router_flavor in topology_definition_string
        sb_def = topology_definition_string.replace(
            router_flavor, '    flavor: standard.small\n    managed: false\n\n  - name: home-router'
        )
        with pytest.raises(YamlizingError, match='found key `managed`'):
            TopologyDefinition.load(sb_def)


class TestVpnRules:
    """
    Tests for the VPN schema rules.
    """

    @pytest.mark.parametrize('domain', ['local', 'sandbox.local', 'a-1.example.com'])
    def test_search_domain_accepted(self, topology_definition_string: str, domain: str) -> None:
        """
        Single- and multi-label domains are valid search domains.
        """
        td = TopologyDefinition.load(
            topology_definition_string
            + f'\nvpn:\n  dns:\n    servers: [10.10.20.5]\n    search_domains: [{domain}]\n'
        )
        assert td.vpn is not None
        assert td.vpn.dns is not None
        assert list(td.vpn.dns.search_domains) == [domain]

    def test_search_domain_with_trailing_newline_rejected(
        self, topology_definition_string: str
    ) -> None:
        """
        A search domain must match the domain pattern in full, trailing newline included.
        """
        with pytest.raises(YamlizingError, match='contains invalid domain'):
            TopologyDefinition.load(
                topology_definition_string
                + '\nvpn:\n  dns:\n    servers: [10.10.20.5]\n'
                + '    search_domains: ["sandbox.local\\n"]\n'
            )
