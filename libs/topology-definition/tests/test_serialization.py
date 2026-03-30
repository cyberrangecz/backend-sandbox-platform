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

        assert len(topology_definition_monitoring.monitoring_targets) == 2
        home = topology_definition_monitoring.monitoring_targets[0]
        assert len(home.targets) == 2
        assert home.targets[0].port == 2022
        assert home.targets[1].port == 2023
        assert home.targets[0].interface == 'eth0'
        assert home.targets[1].interface == 'eth1'
        router = topology_definition_monitoring.monitoring_targets[1]
        assert len(router.targets) == 1
        assert router.node == 'server-router'
        assert router.targets[0].port == 555
        assert router.targets[0].interface == 'eth0'

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
