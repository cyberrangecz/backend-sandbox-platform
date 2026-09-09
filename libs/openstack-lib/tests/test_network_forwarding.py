"""Tests for crczp.openstack_driver.network_forwarding module."""

import pytest

from crczp.cloud_commons import CrczpException
from crczp.openstack_driver.network_forwarding import (
    MAX_TUNNELS_PER_SANDBOX,
    build_tap_mirror_plan,
    router_interface_ip,
    tunnel_id_base,
    validate_router_interface_addresses,
)


class TestRouterInterfaceIp:
    """Unit tests for router_interface_ip."""

    @pytest.mark.parametrize(
        ('cidr', 'expected'),
        [
            ('10.10.40.0/24', '10.10.40.3'),
            ('172.16.8.0/22', '172.16.8.3'),
            ('192.168.0.0/16', '192.168.0.3'),
            # The smallest prefix that can spare an address for the router interface.
            ('10.0.0.0/29', '10.0.0.3'),
            # Host bits are normalised away, matching how Neutron handles a cidr.
            ('10.10.40.5/24', '10.10.40.3'),
        ],
    )
    def test_offset_of_destination_subnet(self, cidr, expected):
        """The pinned address is the fourth address of the destination subnet."""
        assert router_interface_ip('monitoring-switch', cidr) == expected

    @pytest.mark.parametrize('cidr', ['10.0.0.0/30', '10.0.0.0/31', '10.0.0.4/32'])
    def test_subnet_too_small(self, cidr):
        """A subnet with no address to spare is rejected, not silently mis-addressed."""
        # A /30's fourth address is its broadcast address and a /31 has none at all, so an
        # unguarded index would hand Neutron an invalid address or raise IndexError.
        with pytest.raises(CrczpException) as exc_info:
            router_interface_ip('monitoring-switch', cidr)

        assert 'monitoring-switch' in str(exc_info.value)
        assert '/29' in str(exc_info.value)

    def test_unparseable_cidr(self):
        """An unparseable cidr names the network it came from."""
        with pytest.raises(CrczpException) as exc_info:
            router_interface_ip('monitoring-switch', 'not-a-cidr')

        assert 'monitoring-switch' in str(exc_info.value)
        assert 'not-a-cidr' in str(exc_info.value)


class TestValidateRouterInterfaceAddresses:
    """Unit tests for validate_router_interface_addresses."""

    def test_shipped_definition_passes(self, topology_instance_forwarding):
        """The example definition (router .1, destination host .5) leaves .3 free."""
        validate_router_interface_addresses(
            topology_instance_forwarding.get_network_forwarding(), topology_instance_forwarding
        )

    @pytest.mark.parametrize('mapped_node', ['monitoring', 'server-router'])
    def test_reserved_address_taken(self, topology_instance_forwarding, mapped_node):
        """A host or a router mapped to the reserved address is rejected, naming the node."""
        network = topology_instance_forwarding.get_network('monitoring-switch')
        (link,) = [
            link
            for link in topology_instance_forwarding.get_network_links(network)
            if link.node.name == mapped_node
        ]
        link.ip = '10.10.40.3'

        with pytest.raises(CrczpException) as exc_info:
            validate_router_interface_addresses(
                topology_instance_forwarding.get_network_forwarding(), topology_instance_forwarding
            )

        assert '10.10.40.3' in str(exc_info.value)
        assert mapped_node in str(exc_info.value)
        assert 'monitoring-switch' in str(exc_info.value)

    def test_reserved_address_compared_as_address_not_string(self, topology_instance_forwarding):
        """An equivalent but differently spelled mapping is still caught."""
        network = topology_instance_forwarding.get_network('monitoring-switch')
        network.cidr = 'fd00::/64'
        (link,) = [
            link
            for link in topology_instance_forwarding.get_network_links(network)
            if link.node.name == 'monitoring'
        ]
        # The expanded form of fd00::3, which a string comparison would miss.
        link.ip = 'fd00:0:0:0:0:0:0:3'

        with pytest.raises(CrczpException) as exc_info:
            validate_router_interface_addresses(
                topology_instance_forwarding.get_network_forwarding(), topology_instance_forwarding
            )

        assert 'fd00::3' in str(exc_info.value)

    def test_same_offset_on_another_network_is_ignored(self, topology_instance_forwarding):
        """Only the destination network's own addresses are reserved."""
        network = topology_instance_forwarding.get_network('server-switch')
        (link,) = [
            link
            for link in topology_instance_forwarding.get_network_links(network)
            if link.node.name == 'server'
        ]
        link.ip = '10.10.20.3'

        validate_router_interface_addresses(
            topology_instance_forwarding.get_network_forwarding(), topology_instance_forwarding
        )

    def test_no_forwarding_is_a_no_op(self, topology_instance):
        """A topology without a forwarding rule reserves nothing."""
        validate_router_interface_addresses(None, topology_instance)


class TestBuildTapMirrorPlan:
    """Unit tests for build_tap_mirror_plan."""

    def test_router_interface_is_pinned(self, topology_instance_forwarding):
        """The plan carries the pinned address for the destination network."""
        plan = build_tap_mirror_plan(
            topology_instance_forwarding.get_network_forwarding(), 'stack-p1-s2', 'gre'
        )

        assert plan.router_interface is not None
        assert plan.router_interface.fixed_ip == '10.10.40.3'
        assert plan.router_interface.port_name == 'stack-p1-s2-tapm-rp-monitoring-switch'
        assert plan.router_interface.subnet_name == 'stack-p1-s2-monitoring-switch-subnet'

    def test_one_tap_mirror_per_source(self, topology_instance_forwarding):
        """Every source interface gets its own tap_mirror against the single floating IP."""
        rule = topology_instance_forwarding.get_network_forwarding()

        plan = build_tap_mirror_plan(rule, 'stack-p1-s2', 'gre')

        assert plan.floating_ip is not None
        assert [mirror.name for mirror in plan.tap_mirrors] == [
            f'stack-p1-s2-tapm-{index}' for index in range(len(rule.sources))
        ]
        assert {mirror.fip_name for mirror in plan.tap_mirrors} == {plan.floating_ip.fip_name}
        assert plan.destination_port_names == frozenset({plan.floating_ip.dest_port_name})

    def test_mirror_type_propagates_to_every_tap_mirror(self, topology_instance_forwarding):
        """The deployment-supplied mirror_type lands on every tap_mirror."""
        rule = topology_instance_forwarding.get_network_forwarding()

        plan = build_tap_mirror_plan(rule, 'stack-p1-s2', 'erspanv1')

        assert plan.tap_mirrors
        assert {mirror.mirror_type for mirror in plan.tap_mirrors} == {'erspanv1'}

    def test_no_forwarding_yields_an_empty_plan(self):
        """Without a rule the plan renders nothing, but still names the sandbox resources."""
        plan = build_tap_mirror_plan(None, 'stack-p1-s2', 'gre')

        assert plan.router_interface is None
        assert plan.floating_ip is None
        assert plan.tap_mirrors == []
        assert plan.destination_port_names == frozenset()

    def test_destination_network_too_small(self, topology_instance_forwarding):
        """A destination network that cannot spare an address fails the whole plan."""
        rule = topology_instance_forwarding.get_network_forwarding()
        rule.destination.network.cidr = '10.10.40.0/30'

        with pytest.raises(CrczpException) as exc_info:
            build_tap_mirror_plan(rule, 'stack-p1-s2', 'gre')

        assert '/29' in str(exc_info.value)

    def test_tunnel_id_base_from_sandbox_id(self):
        """Tunnel ids are derived from the sandbox id in the resource prefix."""
        assert tunnel_id_base('stack-p0000000001-s0000000002') == 2 * MAX_TUNNELS_PER_SANDBOX
        assert tunnel_id_base('stack-name') == 0
