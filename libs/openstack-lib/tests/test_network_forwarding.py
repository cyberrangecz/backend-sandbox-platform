"""Tests for crczp.openstack_driver.network_forwarding module."""

import re

import pytest
import yaml

from crczp.cloud_commons import CrczpException, InvalidTopologyDefinition, TopologyInstance
from crczp.openstack_driver import network_forwarding
from crczp.openstack_driver.network_forwarding import (
    build_tap_mirrors,
    tunnel_id_base,
    validate_destination_network,
)


def monitoring_targets(protocol: str, node: str, target: dict) -> str:
    """Return a monitoring_targets section with a single target for one node."""
    return yaml.safe_dump({'monitoring_targets': {protocol: [{'node': node, 'targets': [target]}]}})


def http_targets(url: str) -> str:
    """Return a monitoring_targets section with a single HTTP target."""
    return yaml.safe_dump({'monitoring_targets': {'http': {'targets': [{'url': url}]}}})


_FIRST_MAPPING_ON_DESTINATION = re.escape(
    '"monitoring": its first net_mapping is on the destination network "monitoring-switch"'
)


class TestValidateDestinationNetwork:
    """Unit tests for validate_destination_network."""

    @staticmethod
    def validate(instance):
        """Run the validation against the rule of an instance."""
        validate_destination_network(instance.get_network_forwarding(), instance)

    def test_shipped_definition_passes(self, topology_instance_forwarding):
        """The destination host plus the router on the network is the supported layout."""
        self.validate(topology_instance_forwarding)

    def test_no_forwarding_is_a_no_op(self, topology_instance):
        """Without a forwarding rule no network is special."""
        validate_destination_network(None, topology_instance)

    def test_router_as_destination(self, forwarding_definition, trc):
        """A router cannot be the destination: the no-egress mirror group would cut it off."""
        forwarding_definition.network_forwarding.destination.node = 'server-router'

        with pytest.raises(InvalidTopologyDefinition, match='"server-router" on network'):
            self.validate(TopologyInstance(forwarding_definition, trc))

    def test_destination_without_another_user_network(self, forwarding_definition, trc):
        """A destination host whose only user network is the destination network is rejected."""
        mappings = forwarding_definition.net_mappings
        (index,) = [
            index
            for index, mapping in enumerate(mappings)
            if mapping.host == 'monitoring' and mapping.network == 'server-switch'
        ]
        del mappings[index]

        with pytest.raises(InvalidTopologyDefinition, match=_FIRST_MAPPING_ON_DESTINATION):
            self.validate(TopologyInstance(forwarding_definition, trc))

    def test_destination_first_mapping_on_destination_network(self, forwarding_definition, trc):
        """The destination network cannot carry the destination host's sandbox traffic."""
        mappings = forwarding_definition.net_mappings
        first, second = [
            index for index, mapping in enumerate(mappings) if mapping.host == 'monitoring'
        ]
        mappings[first], mappings[second] = mappings[second], mappings[first]
        assert mappings[first].network == 'monitoring-switch'

        with pytest.raises(InvalidTopologyDefinition, match=_FIRST_MAPPING_ON_DESTINATION):
            self.validate(TopologyInstance(forwarding_definition, trc))

    def test_other_host_on_destination_network(
        self, build_forwarding_instance, server_on_monitoring_switch
    ):
        """Another host on the destination network is rejected, naming that host."""
        instance = build_forwarding_instance(extra_net_mappings=server_on_monitoring_switch)

        with pytest.raises(InvalidTopologyDefinition, match='"server" is mapped to it'):
            self.validate(instance)

    @pytest.mark.parametrize(
        ('protocol', 'target'),
        [
            ('icmp', {'address': '10.10.40.5'}),
            ('icmp', {'address': '10.10.40.0/24'}),
            ('icmp', {'address': '10.10.0.0/16'}),
            ('tcp', {'address': '10.10.40.5', 'port': 22}),
        ],
    )
    def test_monitoring_destination_inside_network(
        self, build_forwarding_instance, protocol, target
    ):
        """An address that falls inside the destination network cannot be probed."""
        instance = build_forwarding_instance(
            extra_sections=monitoring_targets(protocol, 'monitoring', target)
        )

        with pytest.raises(
            InvalidTopologyDefinition,
            match=re.escape(f'"monitoring" cannot be monitored at {target["address"]}'),
        ):
            self.validate(instance)

    @pytest.mark.parametrize(
        ('protocol', 'node', 'target'),
        [
            ('icmp', 'monitoring', {'address': '10.10.20.6'}),
            ('icmp', 'monitoring', {'interface': 'ens4'}),
            ('tcp', 'monitoring', {'address': '10.10.20.6', 'port': 22}),
            ('tcp', 'monitoring', {'interface': 'ens4', 'port': 22}),
            # A router's address on that network is reached through its other interfaces.
            ('icmp', 'server-router', {'address': '10.10.40.5'}),
            ('icmp', 'monitoring', {'address': 'not-an-address'}),
        ],
    )
    def test_monitoring_target_outside_network(
        self, build_forwarding_instance, protocol, node, target
    ):
        """Interface-based targets, other networks and other nodes are not affected."""
        instance = build_forwarding_instance(
            extra_sections=monitoring_targets(protocol, node, target)
        )

        self.validate(instance)

    @pytest.mark.parametrize(
        'url',
        [
            'http://10.10.40.5/',
            'https://10.10.40.200:8443/health?x=1',
            'http://user:secret@10.10.40.5/',
        ],
    )
    def test_http_monitoring_inside_network(self, build_forwarding_instance, url):
        """An HTTP target whose host is an address of the destination network is rejected."""
        instance = build_forwarding_instance(extra_sections=http_targets(url))

        with pytest.raises(InvalidTopologyDefinition, match=re.escape(f'"{url}" cannot be')):
            self.validate(instance)

    @pytest.mark.parametrize(
        'url',
        [
            'http://10.10.20.5/',
            'http://monitoring.example.org/',
            'http://[2001:db8::1]/',
            'http://10.10.40.5.example.org/',
        ],
    )
    def test_http_monitoring_outside_network(self, build_forwarding_instance, url):
        """HTTP targets on other networks or addressed by name are not affected."""
        self.validate(build_forwarding_instance(extra_sections=http_targets(url)))

    def test_unmanaged_destination_passes(self, build_forwarding_instance):
        """A destination host without management is the appliance use case and is accepted."""
        instance = build_forwarding_instance(unmanaged_host='monitoring')

        self.validate(instance)


class TestBuildTapMirrors:
    """Unit tests for build_tap_mirrors."""

    @pytest.mark.parametrize(
        ('direction', 'expected'),
        [
            ('in', [(2048, None), (2049, None)]),
            ('out', [(None, 2048), (None, 2049)]),
            ('both', [(2048, 2049), (2050, 2051)]),
        ],
    )
    def test_tunnel_ids_per_direction(self, topology_instance_forwarding, direction, expected):
        """Each source takes the next free ids of the sandbox block, in source order."""
        rule = topology_instance_forwarding.get_network_forwarding()
        network = topology_instance_forwarding.get_network('server-switch')
        (router_link,) = [
            link
            for link in topology_instance_forwarding.get_network_links(network)
            if link.node.name == 'server-router'
        ]
        rule.sources.append(router_link)
        rule.direction = direction

        tap_mirrors = build_tap_mirrors(rule, 2048)

        assert [mirror.source for mirror in tap_mirrors] == rule.sources
        assert [(m.tunnel_id_in, m.tunnel_id_out) for m in tap_mirrors] == expected

    def test_too_many_tunnel_ids(self, monkeypatch, topology_instance_forwarding):
        """A sandbox needing more ids than its block is an invalid topology."""
        monkeypatch.setattr(network_forwarding, 'MAX_TUNNELS_PER_SANDBOX', 1)

        with pytest.raises(InvalidTopologyDefinition, match='needs 2 tunnel ids but only 1'):
            build_tap_mirrors(topology_instance_forwarding.get_network_forwarding(), 0)


class TestTunnelIdBase:
    """Unit tests for tunnel_id_base."""

    def test_from_sandbox_id(self):
        """The base is the sandbox id from the stack name times the block size."""
        assert tunnel_id_base('crczp-p0000000001-s0000000002') == 2048

    @pytest.mark.parametrize('prefix', ['stack-name', 'my-stack', 'crczp-p0000000001', 'crczp-s2'])
    def test_prefix_without_sandbox_id(self, prefix):
        """A stack name without a sandbox id is a caller error, not a topology one."""
        with pytest.raises(CrczpException, match=prefix) as exc_info:
            tunnel_id_base(prefix)

        assert not isinstance(exc_info.value, InvalidTopologyDefinition)
