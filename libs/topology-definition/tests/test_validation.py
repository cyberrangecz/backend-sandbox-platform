"""
Tests for the topology definition checks that span several attributes.
"""

import os
import re

import pytest
from ruamel.yaml import YAML
from yamlize.yamlizing_error import YamlizingError

from crczp.topology_definition.models import TopologyDefinition

ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'assets')


@pytest.fixture(name='topology_definition_string')
def fixture_topology_definition_string() -> str:
    """
    Fixture for the base topology definition string.
    """
    with open(os.path.join(ASSETS_DIR, 'topology.yml'), encoding='utf-8') as f:
        return f.read()


def _error_start_line(error: YamlizingError) -> int:
    match = re.search(r'start:.*?line (\d+)', str(error))
    assert match is not None
    return int(match.group(1))


def _top_level_blocks(document: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    key = ''
    for line in document.splitlines(keepends=True):
        if line[:1].isalpha():
            key = line.split(':', 1)[0]
            blocks[key] = ''
        blocks[key] += line
    return blocks


def _reorder(document: str, order: list[str]) -> str:
    blocks = _top_level_blocks(document)
    assert sorted(order) == sorted(blocks)
    return ''.join(blocks[key].rstrip('\n') + '\n\n' for key in order)


AFTER_ROUTER_MAPPINGS = [
    'name',
    'hosts',
    'routers',
    'groups',
    'router_mappings',
    'net_mappings',
    'networks',
    'wan',
]
HCL_PAYLOAD = (
    '\\"\\n}\\nresource \\"terraform_data\\" \\"pwn\\" {\\n'
    '  provisioner \\"local-exec\\" {\\n    command = \\"id\\"\\n  }\\n}\\n'
    'locals {\\n  tail = \\"1'
)


class TestKeyOrder:
    """
    Checks spanning several attributes run after the whole document is loaded.
    """

    @pytest.mark.parametrize(
        'cidr',
        [f'"100.100.100.0/29{HCL_PAYLOAD}/29"', f'"fe80::%{HCL_PAYLOAD}/64"'],
        ids=['ipv4-suffix', 'ipv6-scope-id'],
    )
    def test_injected_wan_cidr_after_router_mappings_rejected(
        self, topology_definition_string: str, cidr: str
    ) -> None:
        """
        A WAN CIDR written after router_mappings is still validated, so it cannot inject HCL.
        """
        document = _reorder(topology_definition_string, AFTER_ROUTER_MAPPINGS).replace(
            'cidr: 100.100.100.0/29', f'cidr: {cidr}'
        )
        assert 'terraform_data' in document

        with pytest.raises(YamlizingError, match='is not a valid IPv4 CIDR'):
            TopologyDefinition.load(document)

    def test_injected_mapping_ip_scope_id_rejected(self, topology_definition_string: str) -> None:
        """
        A mapping IP is IPv4 only, so an IPv6 scope id cannot carry HCL into the template.
        """
        document = _reorder(topology_definition_string, AFTER_ROUTER_MAPPINGS).replace(
            'ip: 10.10.50.5', f'ip: "fd00:10::5%{HCL_PAYLOAD}"'
        )
        assert 'terraform_data' in document

        with pytest.raises(YamlizingError, match='is not a valid IPv4 address'):
            TopologyDefinition.load(document)

    def test_ipv6_network_rejected(self, topology_definition_string: str) -> None:
        """
        User networks are IPv4 only.
        """
        with pytest.raises(YamlizingError, match='"fd00:10::/64" is not a valid IPv4 CIDR'):
            TopologyDefinition.load(
                topology_definition_string.replace('cidr: 10.10.50.0/24', 'cidr: fd00:10::/64')
            )

    def test_invalid_network_cidr_after_router_mappings_rejected(
        self, topology_definition_string: str
    ) -> None:
        """
        A network CIDR written after router_mappings is still validated.
        """
        document = _reorder(topology_definition_string, AFTER_ROUTER_MAPPINGS).replace(
            'cidr: 10.10.50.0/24', 'cidr: not-a-cidr'
        )

        with pytest.raises(YamlizingError, match='is not a valid IPv4 CIDR'):
            TopologyDefinition.load(document)

    @pytest.mark.parametrize(
        ('ip', 'match'),
        [
            ('not-an-ip', 'is not a valid IPv4 address'),
            ('10.10.99.5', 'is not valid host address'),
        ],
        ids=['malformed', 'outside-network'],
    )
    def test_invalid_net_mapping_ip_after_router_mappings_rejected(
        self, topology_definition_string: str, ip: str, match: str
    ) -> None:
        """
        A net_mappings IP written after router_mappings is still validated.
        """
        document = _reorder(topology_definition_string, AFTER_ROUTER_MAPPINGS).replace(
            'ip: 10.10.50.5', f'ip: {ip}'
        )

        with pytest.raises(YamlizingError, match=match):
            TopologyDefinition.load(document)

    def test_every_mapping_ip_checked(self, topology_definition_string: str) -> None:
        """
        Each mapping IP is checked against its network, not only the last one per network.
        """
        server_mapping = '    network: server-switch\n    ip: 10.10.20.5\n'
        assert topology_definition_string.count(server_mapping) == 1
        document = topology_definition_string.replace(
            server_mapping,
            '    network: server-switch\n    ip: 10.10.99.5\n\n'
            '  - host: home\n    network: server-switch\n    ip: 10.10.20.6\n',
        )

        with pytest.raises(YamlizingError, match='"10.10.99.5" is not valid host address'):
            TopologyDefinition.load(document)

    def test_shuffled_keys_load_the_same_definition(self, topology_definition_string: str) -> None:
        """
        References are resolved after loading, so groups and mappings may precede what they name.
        """
        shuffled = _reorder(
            topology_definition_string,
            [
                'groups',
                'net_mappings',
                'router_mappings',
                'name',
                'networks',
                'wan',
                'routers',
                'hosts',
            ],
        )
        safe_yaml = YAML(typ='safe', pure=True)

        assert safe_yaml.load(
            TopologyDefinition.dump(TopologyDefinition.load(shuffled))
        ) == safe_yaml.load(
            TopologyDefinition.dump(TopologyDefinition.load(topology_definition_string))
        )

    def test_unknown_group_node_before_hosts_rejected(
        self, topology_definition_string: str
    ) -> None:
        """
        A group written before hosts still reports a node that does not exist.
        """
        order = list(_top_level_blocks(topology_definition_string))
        order.remove('groups')
        document = _reorder(topology_definition_string, ['groups', *order]).replace(
            '      - home-router\n', '      - nobody\n'
        )

        with pytest.raises(YamlizingError, match='Cannot find a node .* with name "nobody"'):
            TopologyDefinition.load(document)

    @pytest.mark.parametrize(
        ('old', 'new', 'key'),
        [
            ('      - home-router\n', '      - nobody\n', 'groups'),
            (
                '    network: capture-switch\n    ip: 10.10.50.1',
                '    network: nowhere\n    ip: 10.10.50.1',
                'router_mappings',
            ),
            ('  - name: capture-switch\n', '  - name: server-switch\n', 'networks'),
            ('    ip: 10.10.50.5\n', '    ip: 10.10.99.5\n', 'net_mappings'),
        ],
        ids=['groups', 'router_mappings', 'networks', 'net_mappings'],
    )
    def test_error_points_at_offending_block(
        self, topology_definition_string: str, old: str, new: str, key: str
    ) -> None:
        """
        A cross-attribute error marks the block of the checked key, not the document start.
        """
        assert topology_definition_string.count(old) == 1
        document = topology_definition_string.replace(old, new)

        with pytest.raises(YamlizingError) as exc_info:
            TopologyDefinition.load(document)

        lines = document.splitlines()
        assert _error_start_line(exc_info.value) == lines.index(f'{key}:') + 2

    @pytest.mark.parametrize(
        ('old', 'new'),
        [('  cidr: 100.100.100.0/29', '  cidr: bad'), ('    ip: 10.10.30.1', '    ip: bad')],
        ids=['wan-cidr', 'router-mapping-ip'],
    )
    def test_malformed_value_error_points_at_value(
        self, topology_definition_string: str, old: str, new: str
    ) -> None:
        """
        A malformed CIDR or IP is reported on its own line.
        """
        document = topology_definition_string.replace(old, new)

        with pytest.raises(YamlizingError) as exc_info:
            TopologyDefinition.load(document)

        assert _error_start_line(exc_info.value) == document.splitlines().index(new) + 1


class TestReservedNames:
    """
    The management node and network names cannot be used by the definition.
    """

    @pytest.mark.parametrize(
        ('old', 'new'),
        [
            ('  - name: server\n', '  - name: man\n'),
            ('  - name: home-router\n', '  - name: man\n'),
            ('  - name: capture-switch\n', '  - name: man-network\n'),
            ('  - name: capture-switch\n', '  - name: man\n'),
            ('  name: internet-connection\n', '  name: man-network\n'),
        ],
        ids=['host', 'router', 'network', 'network-named-man', 'wan'],
    )
    def test_reserved_name_rejected(
        self, topology_definition_string: str, old: str, new: str
    ) -> None:
        """
        A host, router, network or WAN named man or man-network is rejected.
        """
        assert topology_definition_string.count(old) == 1

        with pytest.raises(YamlizingError, match='is reserved for the sandbox management'):
            TopologyDefinition.load(topology_definition_string.replace(old, new))

    def test_names_containing_reserved_ones_accepted(self, topology_definition_string: str) -> None:
        """
        Only the exact names are reserved.
        """
        td = TopologyDefinition.load(
            topology_definition_string
            .replace('  - name: server\n', '  - name: manager\n')
            .replace('host: server\n', 'host: manager\n')
            .replace('      - server\n', '      - manager\n')
            .replace('  - name: capture-switch\n', '  - name: man-net\n')
            .replace('network: capture-switch\n', 'network: man-net\n')
        )
        assert td.find_host_by_name('manager') is not None
        assert td.find_network_by_name('man-net') is not None
