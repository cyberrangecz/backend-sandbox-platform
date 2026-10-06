"""
Module for topology definition validators.
"""

from __future__ import annotations

import re
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from itertools import combinations
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from yamlize import StrList

    from crczp.topology_definition.models import (
        WAN,
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
MAX_VOLUMES = 12
# A DNS domain: dot-separated labels, each 1-63 chars, starting and ending with
# an alphanumeric. Accepts single-label domains (e.g. "local") as valid search domains.
DNS_DOMAIN_REGEX = (
    r'^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)'
    r'(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$'
)
# python-commons adds the management node and network under these names.
RESERVED_NAMES = ('man', 'man-network')
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
        if not re.fullmatch(VALID_NAMES_REGEX, name):
            _msg = 'Cannot set {}.name to "{}". It does not match regex "{}".'
            raise ValueError(_msg.format(obj.__class__.__name__, name, VALID_NAMES_REGEX))

    @staticmethod
    def is_ipv4_cidr(obj: object, cidr: str) -> None:
        """
        Validate that cidr is an IPv4 network.
        """
        # Templates render the value raw, and ip_network accepts IPv6 scope ids with any text.
        try:
            IPv4Network(cidr, strict=False)
        except ValueError as exc:
            raise ValueError(
                f'{obj.__class__.__name__}.cidr "{cidr}" is not a valid IPv4 CIDR.'
            ) from exc

    @staticmethod
    def is_ipv4_address(obj: object, ip: str) -> None:
        """
        Validate that ip is an IPv4 address.
        """
        try:
            IPv4Address(ip)
        except ValueError as exc:
            raise ValueError(
                f'{obj.__class__.__name__}.ip "{ip}" is not a valid IPv4 address.'
            ) from exc

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
        TopologyValidation.raise_if_not_in_network(net_mappings, obj.networks)
        return True

    @staticmethod
    def validate_network_forwarding(
        obj: TopologyDefinition, rule: NetworkForwardingRule | None
    ) -> None:
        """
        Validate that the network forwarding rule is well-formed and references existing
        interfaces of the topology.
        """
        if rule is None:
            return
        if not rule.sources:
            raise ValueError('network_forwarding must have at least one source.')

        sources = [(src.node, src.network) for src in rule.sources]
        seen: set[tuple[str, str]] = set()
        for node, network in sources:
            if (node, network) in seen:
                raise ValueError(
                    f'network_forwarding lists source {node}:{network} more than once.'
                )
            seen.add((node, network))

        destination = (rule.destination.node, rule.destination.network)
        interfaces = [('destination', destination)] + [('source', src) for src in sources]
        for role, (node, network) in interfaces:
            if not obj.find_host_by_name(node) and not obj.find_router_by_name(node):
                raise ValueError(
                    f'network_forwarding {role} {node}:{network} references unknown node "{node}".'
                )
            mapped_networks = TopologyValidation._node_interface_networks(obj, node)
            if network not in mapped_networks:
                raise ValueError(
                    f'network_forwarding {role} {node}:{network} is invalid: '
                    f'"{node}" has no mapping to network "{network}".'
                )
            if mapped_networks.count(network) > 1:
                raise ValueError(
                    f'network_forwarding {role} {node}:{network} is ambiguous: '
                    f'"{node}" has several interfaces on network "{network}".'
                )

        if destination in seen:
            raise ValueError(
                f'network_forwarding mirrors interface {destination[0]}:{destination[1]} to itself.'
            )

    @staticmethod
    def _node_interface_networks(obj: TopologyDefinition, node: str) -> list[str]:
        """
        Return the user-defined networks a host or router is mapped to.
        """
        # Host and router names share one namespace, so at most one of the lists matches.
        return [
            net_mapping.network for net_mapping in obj.net_mappings if net_mapping.host == node
        ] + [
            router_mapping.network
            for router_mapping in obj.router_mappings
            if router_mapping.router == node
        ]

    @staticmethod
    def validate_router_mappings(
        obj: TopologyDefinition, router_mappings: RouterMappingList
    ) -> bool:
        """
        Validate router mappings and the uniqueness of all mapping IPs.
        """
        TopologyValidation.validate_name_mappings(obj, router_mappings)
        TopologyValidation.raise_if_not_in_network(router_mappings, obj.networks)
        TopologyValidation.raise_if_ip_not_unique(list(obj.net_mappings) + list(router_mappings))
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
    def validate_network_cidrs(obj: TopologyDefinition, networks: NetworkList) -> None:
        """
        Validate that the networks and the WAN do not overlap.
        """
        TopologyValidation.raise_if_overlaps(list(networks) + [obj.wan])

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
        networks_dict = {net.name: ip_network(net.cidr) for net in networks}
        for mapping in mappings:
            ip = ip_address(mapping.ip)
            if ip not in networks_dict[mapping.network]:
                _msg = 'IP address "{}" is not valid host address of "{}" defined in network "{}".'
                raise ValueError(_msg.format(ip, networks_dict[mapping.network], mapping.network))

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
            if not re.fullmatch(VALID_NAMES_REGEX, node):
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
        for name in b + c + d + e:
            if name in RESERVED_NAMES:
                raise ValueError(
                    f'The name "{name}" is reserved for the sandbox management node and network. '
                    'Rename the host, router or network.'
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
        if volumes is None:
            return
        if len(volumes) < 1:
            raise ValueError('Volumes must contain at least one entry for system disk')
        if len(volumes) > MAX_VOLUMES:
            raise ValueError(f'Volumes must not contain more than {MAX_VOLUMES} entries')
        if volumes[0].image is not None:
            raise ValueError(
                'volumes[0] is the system disk and is created from base_box.image; '
                'set base_box.image instead'
            )
        for index, volume in enumerate(volumes):
            if isinstance(volume.size, bool) or volume.size < 1:
                raise ValueError(f'volumes[{index}].size must be a whole number of at least 1 GB')

    @staticmethod
    def validate_monitoring_targets(
        obj: TopologyDefinition, monitoring_targets: MonitoringTargets | None
    ) -> bool:
        """
        Validate monitoring targets — referenced nodes must exist in the topology
        and must not be hosts with managed: false.
        Called with TopologyDefinition as obj, giving access to hosts and routers.
        """
        if monitoring_targets is None:
            return True

        node_names = set(
            [host.name for host in obj.hosts] + [router.name for router in obj.routers]
        )

        unmanaged_hosts = {host.name for host in obj.hosts if not host.managed}

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
                if target.node in unmanaged_hosts:
                    raise ValueError(
                        f'Host "{target.node}" is managed: false, so stage one never '
                        f'gathers its facts and cannot monitor it ({label} MonitoringTarget).'
                    )

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
        check that requires access to hosts and routers. An entrypoint must not
        be a host with managed: false.
        """
        if vpn is None:
            return
        entrypoints = vpn.entrypoints
        if not entrypoints:
            return
        known_node_names = {h.name for h in obj.hosts} | {r.name for r in obj.routers}
        unmanaged_hosts = {h.name for h in obj.hosts if not h.managed}
        for ep in entrypoints:
            if ep.name not in known_node_names:
                raise ValueError(
                    f'vpn.entrypoints references "{ep.name}" '
                    'which does not exist in hosts or routers.'
                )
            if ep.name in unmanaged_hosts:
                raise ValueError(
                    f'vpn.entrypoints references "{ep.name}" which is managed: false, '
                    'so stage one never installs the NetBird agent on it.'
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
            if not re.fullmatch(DNS_DOMAIN_REGEX, domain):
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
