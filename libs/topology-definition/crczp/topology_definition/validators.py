"""
Module for topology definition validators.
"""

from __future__ import annotations

import re
from ipaddress import ip_address, ip_network
from itertools import combinations
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from yamlize import StrList

    from crczp.topology_definition.models import (
        WAN,
        ForwardingInterface,
        GroupList,
        MonitoringTargetICMPList,
        MonitoringTargets,
        MonitoringTargetTCPList,
        Network,
        NetworkForwardingRule,
        NetworkList,
        NetworkMapping,
        NetworkMappingList,
        RouterMapping,
        RouterMappingList,
        TargetHTTPList,
        TargetICMPList,
        TargetTCPList,
        TopologyDefinition,
        VolumeList,
        Vpn,
    )

VALID_NAMES_REGEX = r'^[a-z]([a-z0-9A-Z-])*$'
# A DNS domain: dot-separated labels, each 1-63 chars, starting and ending with
# an alphanumeric. Accepts single-label domains (e.g. "local") as valid search domains.
DNS_DOMAIN_REGEX = (
    r'^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)'
    r'(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$'
)
_UNIQ_MSG = (
    'Uniqueness violation. The following name identifiers are not unique '
    'within the [{}] definition: {}.'
)


class TopologyValidation:  # pylint: disable=too-many-public-methods
    """
    Class for topology definition validation.
    """

    @staticmethod
    def is_valid_ostack_name(obj: object, name: str) -> None:
        """
        Validate OpenStack name.
        """
        if not re.match(VALID_NAMES_REGEX, name):
            _msg = 'Cannot set {}.name to "{}". It does not match regex "{}".'
            raise ValueError(_msg.format(obj.__class__.__name__, name, VALID_NAMES_REGEX))

    @staticmethod
    def validate_net_mappings(obj: TopologyDefinition, net_mappings: NetworkMappingList) -> bool:
        """
        Validate network mappings.
        """
        _msg = 'Invalid network mapping with ip "{}". Cannot find {} with name "{}".'
        for net_mapping in net_mappings:
            if not obj.find_host_by_name(net_mapping.host):
                raise ValueError(_msg.format(net_mapping.ip, 'host', net_mapping.host))
            if not obj.find_network_by_name(net_mapping.network):
                raise ValueError(_msg.format(net_mapping.ip, 'network', net_mapping.network))
        return True

    @staticmethod
    def validate_network_forwarding(
        obj: TopologyDefinition, network_forwarding: NetworkForwardingRule | None
    ) -> None:
        """
        Validate the network forwarding (port mirroring) rule.

        The rule must have a valid ``direction``, a non-empty
        list of sources, and a destination that differs from every source. Every
        referenced interface must be attached to its network (a net/router mapping
        exists). A source may be a host or a router, but the destination must be a
        host and a dedicated interface (see ``_validate_forwarding_destination``).
        """
        if not network_forwarding:
            return
        valid_directions = {'in', 'out', 'both'}
        rule = network_forwarding
        if rule.direction not in valid_directions:
            raise ValueError(
                f'network_forwarding has invalid direction "{rule.direction}". '
                f'Must be one of {sorted(valid_directions)}.'
            )
        if not rule.sources:
            raise ValueError('network_forwarding must have at least one source.')
        TopologyValidation._validate_forwarding_destination(obj, rule)
        for src in rule.sources:
            TopologyValidation._validate_forwarding_interface(obj, src, 'source')
            if src.host == rule.destination.host and src.network == rule.destination.network:
                raise ValueError(
                    f'network_forwarding mirrors interface {src.host}:{src.network} to itself.'
                )

    @staticmethod
    def _node_interface_networks(obj: TopologyDefinition, node: str) -> list[str]:
        """
        Return the networks a host or router is attached to, in topology definition order.

        Only user-defined networks are listed; the management network and WAN interfaces are
        added later, when the topology instance is built. Host and router names share one
        namespace, so exactly one of the two mapping lists can match a given node.
        """
        return [
            net_mapping.network for net_mapping in obj.net_mappings if net_mapping.host == node
        ] + [
            router_mapping.network
            for router_mapping in obj.router_mappings
            if router_mapping.router == node
        ]

    @staticmethod
    def _validate_forwarding_interface(
        obj: TopologyDefinition,
        iface: ForwardingInterface,
        role: str,
    ) -> None:
        """
        Ensure a forwarding interface references a host/router attached to the
        given network (i.e. a corresponding net/router mapping exists).
        """
        if iface.network not in TopologyValidation._node_interface_networks(obj, iface.host):
            raise ValueError(
                f'network_forwarding {role} interface "{iface.host}:{iface.network}" is not a '
                'valid interface: no host or router is attached to that network.'
            )

    @staticmethod
    def _validate_forwarding_destination(
        obj: TopologyDefinition, rule: NetworkForwardingRule
    ) -> None:
        """
        Ensure the destination is a host and a dedicated interface, not the default-routing one.

        Only a host can be a mirror destination: on OpenStack the destination receives a floating
        IP and a security group that admits only the hypervisors, and it relies on host
        default-routing semantics that a router does not provide. Using the node's first interface —
        the one it routes through — would cut it off from the rest of the sandbox, so the
        destination node needs at least two interfaces and the first one is reserved.
        """
        iface = rule.destination
        TopologyValidation._validate_forwarding_interface(obj, iface, 'destination')

        if obj.find_router_by_name(iface.host):
            raise ValueError(
                f'network_forwarding destination interface "{iface.host}:{iface.network}" is '
                f'invalid: "{iface.host}" is a router. Only a host can be a mirror destination — '
                'the destination receives a floating IP and a hypervisor-only security group and '
                'relies on host default-routing semantics that a router does not provide.'
            )

        networks = TopologyValidation._node_interface_networks(obj, iface.host)
        if len(networks) < 2:
            raise ValueError(
                f'network_forwarding destination interface "{iface.host}:{iface.network}" is '
                f'invalid: "{iface.host}" has only one interface in the topology definition. '
                'A mirror destination needs at least two: the first one for default routing '
                'and a dedicated one for the mirrored traffic.'
            )
        if iface.network == networks[0]:
            raise ValueError(
                f'network_forwarding destination interface "{iface.host}:{iface.network}" is '
                f'invalid: "{networks[0]}" is the first interface of "{iface.host}" and is '
                'used for default routing. Mirror the traffic to one of its other '
                f'interfaces: {networks[1:]}.'
            )

    @staticmethod
    def validate_router_mappings(
        obj: TopologyDefinition, router_mappings: RouterMappingList
    ) -> bool:
        """
        Validate router mappings.
        """
        TopologyValidation.validate_name_mappings(obj, router_mappings)
        TopologyValidation.validate_cidrs_and_ips(obj, router_mappings)

        return True

    @staticmethod
    def validate_name_mappings(obj: TopologyDefinition, router_mappings: RouterMappingList) -> None:
        """
        Validate name mappings.
        """
        _msg = 'Invalid router mapping with ip "{}". Cannot find {} with name "{}".'
        for router_mapping in router_mappings:
            if not obj.find_router_by_name(router_mapping.router):
                raise ValueError(_msg.format(router_mapping.ip, 'router', router_mapping.router))
            if not obj.find_network_by_name(router_mapping.network):
                raise ValueError(_msg.format(router_mapping.ip, 'network', router_mapping.network))

    @staticmethod
    def validate_cidrs_and_ips(obj: TopologyDefinition, router_mappings: RouterMappingList) -> None:
        """
        Validate CIDRs and IP addresses.
        """
        networks = list(obj.networks) + [obj.wan]
        net_mappings = list(obj.net_mappings)
        router_mappings_list = list(router_mappings)

        TopologyValidation.raise_if_overlaps(networks)

        TopologyValidation.raise_if_not_in_network(obj.net_mappings, obj.networks)
        TopologyValidation.raise_if_not_in_network(router_mappings, obj.networks)

        TopologyValidation.raise_if_ip_not_unique(net_mappings + router_mappings_list)

    @staticmethod
    def raise_if_overlaps(networks: list[Network | WAN]) -> None:
        """
        Raise error if networks overlap.
        """
        cidrs = {network.name: ip_network(network.cidr) for network in networks}
        for net_a, net_b in combinations(cidrs, 2):
            if cidrs[net_a].overlaps(cidrs[net_b]):
                _msg = 'Network "{}" overlaps with network "{}".'
                raise ValueError(_msg.format(cidrs[net_a], cidrs[net_b]))

    @staticmethod
    def raise_if_not_in_network(
        mappings: NetworkMappingList | RouterMappingList,
        networks: NetworkList,
    ) -> None:
        """
        Raise error if IP is not in network.
        """
        mappings_dict = {mapp.network: ip_address(mapp.ip) for mapp in mappings}
        networks_dict = {net.name: ip_network(net.cidr) for net in networks}
        for network, ip in mappings_dict.items():
            if ip not in networks_dict[network]:
                _msg = 'IP address "{}" is not valid host address of "{}" defined in network "{}".'
                raise ValueError(_msg.format(ip, networks_dict[network], network))

    @staticmethod
    def raise_if_ip_not_unique(
        mappings: list[NetworkMapping | RouterMapping],
    ) -> None:
        """
        Raise error if IP is not unique.
        """
        mappings_ip = [mapp.ip for mapp in mappings]
        duplicates_ip = TopologyValidation.get_duplicates(mappings_ip)

        if duplicates_ip:
            _msg = (
                'Uniqueness violation. The IP address of either of mappings '
                'must be unique. Incorrect IP addresses: {}.'
            )
            raise ValueError(_msg.format(duplicates_ip))

    @staticmethod
    def validate_groups(obj: TopologyDefinition, groups: GroupList) -> bool:
        """
        Validate groups.
        """
        TopologyValidation.raise_if_not_unique('groups', [g.name for g in groups])

        _msg = 'Invalid group with name "{}". Cannot find a node (host or router) with name "{}".'
        for group in groups:
            for node in group.nodes:
                if not obj.find_host_by_name(node) and not obj.find_router_by_name(node):
                    raise ValueError(_msg.format(group.name, node))
        return True

    @staticmethod
    def validate_group_nodes(_obj: object, nodes: StrList) -> bool:
        """
        Validate group nodes.
        """
        for node in nodes:
            if not re.match(VALID_NAMES_REGEX, node):
                _msg = 'Invalid name "{}" in Group.nodes. It does not match regex "{}".'
                raise ValueError(_msg.format(node, VALID_NAMES_REGEX))

        TopologyValidation.raise_if_not_unique('Group.nodes', list(nodes))

        return True

    @staticmethod
    def validate_name_uniqueness(obj: TopologyDefinition, networks: NetworkList) -> bool:
        """
        Validate name uniqueness.
        """
        a = [obj.name]
        b = [h.name for h in obj.hosts]
        c = [r.name for r in obj.routers]
        d = [n.name for n in networks]
        e = [obj.wan.name]

        TopologyValidation.raise_if_not_unique(
            'name, hosts, routers, networks, wan', a + b + c + d + e
        )

        return True

    @staticmethod
    def raise_if_not_unique(what_for: str, elements: list[str]) -> None:
        """
        Raise error if elements are not unique.
        """
        duplicates = TopologyValidation.get_duplicates(elements)
        if duplicates:
            raise ValueError(_UNIQ_MSG.format(what_for, duplicates))

    @staticmethod
    def get_duplicates(elements: list[str]) -> list[str]:
        """
        Get duplicate elements.
        """
        result = set()

        unique_elements = set(elements)

        if len(elements) > len(unique_elements):
            for element in elements:
                if element not in unique_elements:
                    result.add(element)
                else:
                    unique_elements.remove(element)

        return list(result)

    @staticmethod
    def is_volumes_valid(_obj: object, volumes: VolumeList) -> None:
        """
        Validate volumes.
        """
        if volumes is not None and len(volumes) < 1:
            raise ValueError('Volumes must contain at least one entry for system disk')

    @staticmethod
    def validate_monitoring_targets(
        obj: TopologyDefinition, monitoring_targets: MonitoringTargets | None
    ) -> bool:
        """
        Validate monitoring targets — referenced nodes must exist in the topology.
        Called with TopologyDefinition as obj, giving access to hosts and routers.
        """
        if monitoring_targets is None:
            return True

        node_names = set(
            [host.name for host in obj.hosts] + [router.name for router in obj.routers]
        )

        for targets_list, label in (
            (monitoring_targets.tcp or [], 'TCP'),
            (monitoring_targets.icmp or [], 'ICMP'),
        ):
            for target in targets_list:
                if target.node not in node_names:
                    _msg = (
                        'Invalid node name in {} MonitoringTarget.node. '
                        'No node with name "{}" found.'
                    )
                    raise ValueError(_msg.format(label, target.node))

        return True

    @staticmethod
    def validate_monitoring_targets_tcp(
        _obj: object, targets: MonitoringTargetTCPList | None
    ) -> bool:
        """
        Validate TCP monitoring targets — node names must be unique.
        """
        if targets is None:
            return True

        used_node_names: set[str] = set()

        for target in targets:
            if target.node in used_node_names:
                _msg = (
                    'Duplicate node name "{}" in MonitoringTarget.node. '
                    'Only define each target once.'
                )
                raise ValueError(_msg.format(target.node))

            used_node_names.add(target.node)

        return True

    @staticmethod
    def validate_targets_tcp(_obj: object, targets: TargetTCPList | None) -> bool:
        """
        Validate TCP targets — exactly one of interface/address required, port must be valid.
        The same port may appear multiple times (on different interfaces/addresses).
        """
        if targets is None:
            return True

        for target in targets:
            if target.interface is None and target.address is None:
                raise ValueError(
                    'A TCP target must specify exactly one of "interface" or "address".'
                )
            if target.interface is not None and target.address is not None:
                raise ValueError(
                    'A TCP target must specify exactly one of "interface" or "address", not both.'
                )

            if target.port < 1 or target.port > 65535:
                _msg = (
                    'Port "{}" in MonitoringTarget.ports is not a valid port number. '
                    'Port number must be in range <1, 65535>.'
                )
                raise ValueError(_msg.format(target.port))

        return True

    @staticmethod
    def validate_monitoring_targets_icmp(
        _obj: object, targets: MonitoringTargetICMPList | None
    ) -> bool:
        """
        Validate ICMP monitoring targets — node names must be unique.
        Node existence is validated at the TopologyDefinition level via validate_monitoring_targets.
        """
        if targets is None:
            return True

        used_node_names: set[str] = set()

        for target in targets:
            if target.node in used_node_names:
                _msg = (
                    'Duplicate node name "{}" in MonitoringTarget.node. '
                    'Only define each target once.'
                )
                raise ValueError(_msg.format(target.node))

            used_node_names.add(target.node)

        return True

    @staticmethod
    def validate_targets_icmp(_obj: object, targets: TargetICMPList | None) -> bool:
        """
        Validate ICMP targets — exactly one of interface/address required.
        """
        if targets is None:
            return True

        for target in targets:
            if target.interface is None and target.address is None:
                raise ValueError(
                    'An ICMP target must specify exactly one of "interface" or "address".'
                )
            if target.interface is not None and target.address is not None:
                raise ValueError(
                    'An ICMP target must specify exactly one of "interface" or "address", not both.'
                )

        return True

    @staticmethod
    def validate_vpn(obj: TopologyDefinition, vpn: Vpn | None) -> None:
        """
        Validate VPN settings.

        Each entrypoint name must reference an existing host or router. The
        structural validation of entrypoints (name/routes) and DNS (servers/
        search_domains) is handled by the per-attribute validators; this
        TopologyDefinition-level validator only performs the cross-reference
        check that requires access to hosts and routers.
        """
        if vpn is None:
            return
        entrypoints = vpn.entrypoints
        if not entrypoints:
            return
        known_node_names = {h.name for h in obj.hosts} | {r.name for r in obj.routers}
        for ep in entrypoints:
            if ep.name not in known_node_names:
                raise ValueError(
                    f'vpn.entrypoints references "{ep.name}" '
                    'which does not exist in hosts or routers.'
                )

    @staticmethod
    def validate_vpn_dns_servers(_obj: object, servers: StrList) -> None:
        """
        Validate VPN DNS servers: non-empty list, each element a valid IPv4 address.
        """
        if not servers:
            raise ValueError('vpn.dns.servers must be a non-empty list when vpn.dns is set.')
        for server in servers:
            try:
                addr = ip_address(server)
            except ValueError as exc:
                raise ValueError(
                    f'vpn.dns.servers contains invalid IP address "{server}". '
                    'Each DNS server must be a valid IPv4 address.'
                ) from exc
            if addr.version != 4:
                raise ValueError(
                    f'vpn.dns.servers contains non-IPv4 address "{server}". '
                    'Only IPv4 DNS servers are supported.'
                )

    @staticmethod
    def validate_vpn_dns_search_domains(_obj: object, domains: StrList | None) -> None:
        """
        Validate VPN DNS search domains: optional list, each a valid DNS domain.
        """
        if not domains:
            return
        for domain in domains:
            if not re.match(DNS_DOMAIN_REGEX, domain):
                raise ValueError(
                    f'vpn.dns.search_domains contains invalid domain "{domain}". '
                    'Each search domain must be a valid DNS domain name.'
                )

    @staticmethod
    def validate_vpn_entrypoint_name(_obj: object, name: str) -> None:
        """
        Validate VPN entrypoint name is a non-empty string.
        """
        if not name:
            raise ValueError('VpnEntrypoint.name must be a non-empty string.')

    @staticmethod
    def validate_vpn_routes(_obj: object, routes: StrList) -> None:
        """
        Validate VPN entrypoint routes: non-empty list, each element a valid CIDR.
        """
        if not routes:
            raise ValueError('VpnEntrypoint.routes must be a non-empty list.')
        for cidr in routes:
            try:
                network = ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f'VpnEntrypoint.routes contains invalid CIDR "{cidr}". '
                    'Each route must be a valid IPv4 CIDR string.'
                ) from exc
            if network.version != 4:
                raise ValueError(
                    f'VpnEntrypoint.routes contains non-IPv4 CIDR "{cidr}". '
                    'Only IPv4 routes are supported.'
                )

    @staticmethod
    def validate_targets_http(_obj: object, targets: TargetHTTPList | None) -> bool:
        """
        Validate HTTP targets — url must be a valid http/https URL.
        """
        if targets is None:
            return True

        for target in targets:
            parsed = urlparse(target.url)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc:
                _msg = 'HTTP target url "{}" is not a valid http/https URL.'
                raise ValueError(_msg.format(target.url))

        return True
