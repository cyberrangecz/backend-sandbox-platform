"""
Tests for the network_forwarding block of a topology definition.
"""

import os

import pytest
from yamlize.yamlizing_error import YamlizingError

from crczp.topology_definition.models import ForwardingDirection, TopologyDefinition

ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'assets')


@pytest.fixture(name='topology_definition_string')
def fixture_topology_definition_string() -> str:
    """
    Fixture for the base topology definition string, without network_forwarding.
    """
    with open(os.path.join(ASSETS_DIR, 'topology.yml'), encoding='utf-8') as f:
        return f.read()


@pytest.fixture(name='topology_definition')
def fixture_topology_definition(topology_definition_string: str) -> TopologyDefinition:
    """
    Fixture for the base topology definition.
    """
    return TopologyDefinition.load(topology_definition_string)


@pytest.fixture(name='topology_definition_forwarding')
def fixture_topology_definition_forwarding() -> TopologyDefinition:
    """
    Fixture for topology definition with network forwarding.
    """
    return TopologyDefinition.from_file(os.path.join(ASSETS_DIR, 'topology-with-forwarding.yml'))


FORWARDING_RULE = """
network_forwarding:
  sources:
    - { node: server, network: server-switch }
  destination: { node: home, network: capture-switch }
"""


class TestNetworkForwarding:
    """
    Tests for the network_forwarding block.
    """

    def test_absent(self, topology_definition: TopologyDefinition) -> None:
        """
        Topology without a network_forwarding block loads with the attribute None.
        """
        assert topology_definition.network_forwarding is None

    def test_from_file(self, topology_definition_forwarding: TopologyDefinition) -> None:
        """
        A network_forwarding block is parsed: sources, destination, direction.
        """
        rule = topology_definition_forwarding.network_forwarding
        assert rule is not None
        assert rule.direction is ForwardingDirection.BOTH
        assert len(rule.sources) == 2
        assert rule.sources[0].node == 'server'
        assert rule.sources[0].network == 'server-switch'
        assert rule.sources[1].node == 'server-router'
        assert rule.destination.node == 'monitoring'
        assert rule.destination.network == 'monitoring-switch'

    def test_direction_defaults_to_both(self, topology_definition_string: str) -> None:
        """
        direction defaults to both when omitted.
        """
        td = TopologyDefinition.load(topology_definition_string + FORWARDING_RULE)
        assert td.network_forwarding is not None
        assert td.network_forwarding.direction is ForwardingDirection.BOTH

    @pytest.mark.parametrize(
        ('raw', 'expected'),
        [('in', ForwardingDirection.IN), ('out', ForwardingDirection.OUT)],
    )
    def test_explicit_direction_is_enum(
        self, topology_definition_string: str, raw: str, expected: ForwardingDirection
    ) -> None:
        """
        A written direction loads as the ForwardingDirection member, like the default.
        """
        td = TopologyDefinition.load(
            topology_definition_string + FORWARDING_RULE + f'  direction: {raw}\n'
        )
        assert td.network_forwarding is not None
        assert td.network_forwarding.direction is expected

    def test_assigned_direction_round_trips(self, topology_definition_string: str) -> None:
        """
        An assigned ForwardingDirection member is dumped as its plain value and loads back.
        """
        td = TopologyDefinition.load(topology_definition_string + FORWARDING_RULE)
        assert td.network_forwarding is not None
        td.network_forwarding.direction = ForwardingDirection.OUT

        dumped = TopologyDefinition.dump(td)

        assert '  direction: out\n' in dumped
        reloaded = TopologyDefinition.load(dumped).network_forwarding
        assert reloaded is not None
        assert reloaded.direction is ForwardingDirection.OUT

    def test_invalid_direction_rejected(self, topology_definition_string: str) -> None:
        """
        An invalid direction is rejected at its key with the list of allowed values.
        """
        document = topology_definition_string + FORWARDING_RULE + '  direction: sideways\n'
        with pytest.raises(YamlizingError, match=r"\['both', 'in', 'out'\]") as exc_info:
            TopologyDefinition.load(document)
        assert f'line {len(document.splitlines())}, column' in str(exc_info.value)

    def test_list_form_rejected(self, topology_definition_string: str) -> None:
        """
        network_forwarding is a single mapping, so a YAML list is rejected.
        """
        with pytest.raises(YamlizingError, match='Expected a mapping node'):
            TopologyDefinition.load(
                topology_definition_string
                + """
network_forwarding:
  - sources:
      - { node: server, network: server-switch }
    destination: { node: home, network: capture-switch }
"""
            )

    def test_router_source_accepted(self, topology_definition_string: str) -> None:
        """
        A router is a valid mirror source.
        """
        td = TopologyDefinition.load(
            topology_definition_string
            + """
network_forwarding:
  sources:
    - { node: server-router, network: server-switch }
  destination: { node: home, network: capture-switch }
"""
        )
        assert td.network_forwarding is not None
        assert td.network_forwarding.sources[0].node == 'server-router'

    @pytest.mark.parametrize(
        ('rule_yaml', 'match'),
        [
            pytest.param(
                """
  sources: []
  destination: { node: home, network: capture-switch }
""",
                'at least one source',
                id='empty-sources',
            ),
            pytest.param(
                """
  destination: { node: home, network: capture-switch }
""",
                r"without default: \['sources'\]",
                id='missing-sources',
            ),
            pytest.param(
                """
  sources:
    - { node: server, network: server-switch }
    - { node: server, network: server-switch }
  destination: { node: home, network: capture-switch }
""",
                'lists source server:server-switch more than once',
                id='duplicate-sources',
            ),
            pytest.param(
                """
  sources:
    - { node: server, network: server-switch }
  destination: { node: nobody, network: capture-switch }
""",
                'destination nobody:capture-switch references unknown node "nobody"',
                id='unknown-destination-node',
            ),
            pytest.param(
                """
  sources:
    - { node: nobody, network: server-switch }
  destination: { node: home, network: capture-switch }
""",
                'source nobody:server-switch references unknown node "nobody"',
                id='unknown-source-node',
            ),
            pytest.param(
                """
  sources:
    - { node: server, network: home-switch }
  destination: { node: home, network: capture-switch }
""",
                '"server" has no mapping to network "home-switch"',
                id='source-without-mapping',
            ),
            pytest.param(
                """
  sources:
    - { node: server, network: server-switch }
  destination: { node: home, network: server-switch }
""",
                '"home" has no mapping to network "server-switch"',
                id='destination-without-mapping',
            ),
            pytest.param(
                """
  sources:
    - { node: server, network: server-switch }
    - { node: home, network: capture-switch }
  destination: { node: home, network: capture-switch }
""",
                'mirrors interface home:capture-switch to itself',
                id='destination-is-a-source',
            ),
        ],
    )
    def test_invalid_rule_rejected(
        self, topology_definition_string: str, rule_yaml: str, match: str
    ) -> None:
        """
        A malformed rule, or one referencing an interface the topology does not have, is rejected.
        """
        with pytest.raises(YamlizingError, match=match):
            TopologyDefinition.load(
                topology_definition_string + '\nnetwork_forwarding:' + rule_yaml
            )

    @pytest.mark.parametrize(
        ('mappings_key', 'extra_mapping', 'interface'),
        [
            (
                'net_mappings:\n',
                '  - host: server\n    network: server-switch\n    ip: 10.10.20.7\n\n',
                'server:server-switch',
            ),
            (
                'router_mappings:\n',
                '  - router: server-router\n    network: server-switch\n    ip: 10.10.20.2\n\n',
                'server-router:server-switch',
            ),
        ],
        ids=['host', 'router'],
    )
    def test_ambiguous_interface_rejected(
        self,
        topology_definition_string: str,
        mappings_key: str,
        extra_mapping: str,
        interface: str,
    ) -> None:
        """
        An interface is named by node and network, so a node with two interfaces on that
        network cannot be mirrored unambiguously.
        """
        node, network = interface.split(':')
        document = topology_definition_string.replace(
            mappings_key, mappings_key + extra_mapping, 1
        ) + (
            '\nnetwork_forwarding:\n'
            f'  sources:\n    - {{ node: {node}, network: {network} }}\n'
            '  destination: { node: home, network: capture-switch }\n'
        )

        with pytest.raises(
            YamlizingError,
            match=f'source {interface} is ambiguous: "{node}" has several interfaces on network',
        ):
            TopologyDefinition.load(document)

    def test_unmanaged_destination_accepted(self, topology_definition_string: str) -> None:
        """
        An unmanaged host, such as an IDS appliance, may be the mirror destination.
        """
        host_line = '  - name: home\n'
        assert host_line in topology_definition_string
        document = topology_definition_string.replace(
            host_line, host_line + '    managed: false\n', 1
        )

        td = TopologyDefinition.load(document + FORWARDING_RULE)

        home = td.find_host_by_name('home')
        assert home is not None
        assert home.managed is False
        assert td.network_forwarding is not None
        assert td.network_forwarding.destination.node == 'home'

    @pytest.mark.parametrize('position', ['first', 'middle', 'last'])
    def test_key_order_independent(self, topology_definition_string: str, position: str) -> None:
        """
        The rule is validated after the whole document is loaded, so it may be written above
        the mappings it references.
        """
        block = FORWARDING_RULE.lstrip('\n')
        if position == 'first':
            document = topology_definition_string.replace(
                'name: small-sandbox\n', 'name: small-sandbox\n' + block, 1
            )
        elif position == 'middle':
            document = topology_definition_string.replace(
                'net_mappings:', block + 'net_mappings:', 1
            )
        else:
            document = topology_definition_string + FORWARDING_RULE
        assert document.count('network_forwarding:') == 1

        rule = TopologyDefinition.load(document).network_forwarding

        assert rule is not None
        assert [(src.node, src.network) for src in rule.sources] == [('server', 'server-switch')]
        assert (rule.destination.node, rule.destination.network) == ('home', 'capture-switch')
        assert rule.direction is ForwardingDirection.BOTH
