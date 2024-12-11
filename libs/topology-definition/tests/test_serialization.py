import io
import os

import pytest
from ruamel.yaml import YAML
from yamlize.yamlizing_error import YamlizingError

from crczp.topology_definition.models import TopologyDefinition, Protocol, BaseBox, Host, Router
from crczp.topology_definition.image_naming import image_name_replace, image_name_strip

SANDBOX_DEFINITION_PATH = os.path.join(os.path.dirname(__file__), 'assets/topology.yml')
SANDBOX_DEFINITION_MONITORING_PATH = os.path.join(os.path.dirname(__file__), 'assets/topology-with-monitoring.yml')


@pytest.fixture
def topology_definition_string():
    with open(SANDBOX_DEFINITION_PATH) as f:
        return f.read()


@pytest.fixture
def topology_definition_dict():
    with open(SANDBOX_DEFINITION_PATH) as f:
        return dict(YAML(typ='safe', pure=True).load(f))


@pytest.fixture
def topology_definition():
    return TopologyDefinition.from_file(SANDBOX_DEFINITION_PATH)


@pytest.fixture
def topology_definition_monitoring():
    return TopologyDefinition.from_file(SANDBOX_DEFINITION_MONITORING_PATH)


@pytest.mark.integration
class TestDummy:
    def test_read_yaml(self, topology_definition):
        assert topology_definition is not None
        assert len(topology_definition.hosts) == 2
        assert not topology_definition.find_network_by_name('home-switch').accessible_by_user
        server = topology_definition.find_host_by_name('server')
        assert server.base_box.mgmt_protocol == Protocol.SSH
        assert server.extra is None
        home = topology_definition.find_host_by_name('home')
        assert home.base_box.mgmt_protocol == Protocol.WINRM
        assert home.extra['hello'] == 'yello'
        assert home.extra['yello'] == 5
        assert home.extra['foo']

    def test_read_yaml_monitoring(self, topology_definition_monitoring):
        assert topology_definition_monitoring is not None
        assert len(topology_definition_monitoring.hosts) == 2
        assert not topology_definition_monitoring.find_network_by_name('home-switch').accessible_by_user

        assert len(topology_definition_monitoring.monitoring_targets) == 2
        home = topology_definition_monitoring.monitoring_targets[0]
        assert len(home.targets) == 2
        assert home.targets[0].port == 2022
        assert home.targets[1].port == 2023
        assert home.targets[0].interface == "eth0"
        assert home.targets[1].interface == "eth1"
        router = topology_definition_monitoring.monitoring_targets[1]
        assert len(router.targets) == 1
        assert router.node == "server-router"
        assert router.targets[0].port == 555
        assert router.targets[0].interface == "eth0"

    def test_indexes(self, topology_definition):
        assert topology_definition.find_host_by_name('server') is not None
        assert topology_definition.find_host_by_name('home') is not None

    def test_read_yaml_bad_protocol(self, topology_definition_string):
        bad_topology_definition_string = topology_definition_string \
            .replace('winrm', 'InvalidProtocol')

        with pytest.raises(ValueError):
            TopologyDefinition.load(bad_topology_definition_string)

    def test_cidr_overlaps(self):
        with open(SANDBOX_DEFINITION_PATH) as f:
            sb_def = f.read().replace('cidr: 100.100.100.0/29', 'cidr: 10.10.20.0/29')

        with pytest.raises(YamlizingError):
            TopologyDefinition.load(sb_def)

    def test_ip_not_in_network(self):
        with open(SANDBOX_DEFINITION_PATH) as f:
            sb_def = f.read().replace('ip: 10.10.20.5', 'ip: 10.10.40.5')

        with pytest.raises(YamlizingError):
            TopologyDefinition.load(sb_def)

    def test_ip_not_unique(self):
        with open(SANDBOX_DEFINITION_PATH) as f:
            sb_def = f.read().replace('ip: 10.10.20.5', 'ip: 10.10.20.1')

        with pytest.raises(YamlizingError):
            TopologyDefinition.load(sb_def)

    def test_multi_protocol_base_box(self, topology_definition_dict):
        server_base_box_dict = topology_definition_dict['hosts'][0]['base_box']
        server_base_box_dict['mng_protocol'] = 'ssh'
        server_base_box_dict['mgmt_protocol'] = 'ssh'

        with pytest.raises(YamlizingError):
            output_stream = io.StringIO()
            YAML(typ='full').dump(server_base_box_dict, output_stream)
            BaseBox.load(output_stream.getvalue())

    def test_multi_user_base_box(self, topology_definition_dict):
        server_base_box_dict = topology_definition_dict['hosts'][0]['base_box']
        server_base_box_dict['man_user'] = 'debian'
        server_base_box_dict['mgmt_user'] = 'debian'

        with pytest.raises(YamlizingError):
            output_stream = io.StringIO()
            YAML(typ='full').dump(server_base_box_dict, output_stream)
            BaseBox.load(output_stream.getvalue())

    def test_deprecated_base_box_attributes(self, topology_definition_dict):
        server_router_base_box_dict = topology_definition_dict['routers'][0]['base_box']
        output_stream = io.StringIO()
        YAML(typ='full').dump(server_router_base_box_dict, output_stream)
        server_router_base_box = BaseBox.load(output_stream.getvalue())

        assert not hasattr(server_router_base_box, 'man_user')
        assert not hasattr(server_router_base_box, 'mng_protocol')
        assert server_router_base_box.mgmt_user
        assert server_router_base_box.mgmt_protocol

    def test_image_name_replace_1(self, topology_definition):
        td = image_name_replace('w', 'X', topology_definition)

        home: Host = td.find_host_by_name('home')
        assert home.base_box.image == 'Xindows/windows-10-amd64'

        home_router: Router = td.find_router_by_name('home-router')
        assert home_router.base_box.image == 'debian/debian-12-x86_64'

    def test_image_name_replace_2(self, topology_definition):
        td = image_name_replace(r'.*/', 'crczp-', topology_definition)

        home: Host = td.find_host_by_name('home')
        assert home.base_box.image == 'crczp-windows-10-amd64'

        home_router: Router = td.find_router_by_name('home-router')
        assert home_router.base_box.image == 'crczp-debian-12-x86_64'

    def test_image_name_strip(self, topology_definition):
        td = image_name_strip('crczp/', topology_definition)

        home: Host = td.find_host_by_name('home')
        assert home.base_box.image == 'windows/windows-10-amd64'

        server_router: Router = td.find_router_by_name('server-router')
        assert server_router.base_box.image == 'debian-12-x86_64'
