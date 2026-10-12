"""
Tests for role-aware visibility and reach composition in TopologyInstance.
"""

# pylint: disable=redefined-outer-name
import pytest
from crczp.topology_definition.models import TopologyDefinition

from crczp.cloud_commons import UNIVERSAL_ROLES, TopologyInstance, TransformationConfiguration

TRC_CONFIG = TransformationConfiguration(
    man_image='debian-12-x86_64', man_flavor='standard.small', man_user='debian'
)

# Visibility: a multihomed host (victim) with two paths. Path A (net-a -> router-a) is
# role-gated and complete for red-team. Path B (net-b -> router-b) is broken at its
# router, which is unconditionally hidden regardless of role.
VISIBILITY_DEFINITION = """
name: role-vis-sandbox
hosts:
  - name: victim
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small
    visible_by_roles: [red-team]

routers:
  - name: router-a
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small
    visible_by_roles: [red-team]

  - name: router-b
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small
    hidden: true

wan:
  name: internet
  cidr: 100.100.100.0/29

networks:
  - name: net-a
    cidr: 10.10.10.0/24
    visible_by_roles: [red-team]

  - name: net-b
    cidr: 10.10.20.0/24

net_mappings:
  - host: victim
    network: net-a
    ip: 10.10.10.5

  - host: victim
    network: net-b
    ip: 10.10.20.5

router_mappings:
  - router: router-a
    network: net-a
    ip: 10.10.10.1

  - router: router-b
    network: net-b
    ip: 10.10.20.1

groups: []
"""

# Reach: a machine on two networks — one universally accessible (boolean), one
# role-gated to blue-team — plus an orphan network whose accessible_by_roles is empty.
REACH_DEFINITION = """
name: role-reach-sandbox
hosts:
  - name: victim
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small

routers:
  - name: gw
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small

wan:
  name: internet
  cidr: 100.100.100.0/29

networks:
  - name: office-lan
    cidr: 10.10.30.0/24
    accessible_by_user: true

  - name: target-lan
    cidr: 10.10.40.0/24
    accessible_by_roles: [blue-team]

  - name: locked-lan
    cidr: 10.10.50.0/24
    accessible_by_roles: []

net_mappings:
  - host: victim
    network: office-lan
    ip: 10.10.30.5

  - host: victim
    network: target-lan
    ip: 10.10.40.5

router_mappings:
  - router: gw
    network: office-lan
    ip: 10.10.30.1

  - router: gw
    network: target-lan
    ip: 10.10.40.1

groups: []
"""


@pytest.fixture
def visibility_topology_instance() -> TopologyInstance:
    """Topology instance exercising visibility composition along two paths."""
    topology_definition = TopologyDefinition.load(VISIBILITY_DEFINITION)
    return TopologyInstance(topology_definition, TRC_CONFIG)


@pytest.fixture
def reach_topology_instance() -> TopologyInstance:
    """Topology instance exercising reach composition across two networks."""
    topology_definition = TopologyDefinition.load(REACH_DEFINITION)
    return TopologyInstance(topology_definition, TRC_CONFIG)


# --- Declaration evaluation (§2.1) ---------------------------------------------------


def test_role_list_admits_on_partial_overlap(
    visibility_topology_instance: TopologyInstance,
) -> None:
    """A role list admits when it shares at least one name with users_roles."""
    ti = visibility_topology_instance
    victim = ti.get_node('victim')
    assert victim in ti.get_visible_hosts(frozenset({'red-team', 'green-team'}))


def test_role_list_denies_on_disjoint_sets(visibility_topology_instance: TopologyInstance) -> None:
    """A role list denies when it shares no name with users_roles."""
    ti = visibility_topology_instance
    victim = ti.get_node('victim')
    assert victim not in ti.get_visible_hosts(frozenset({'blue-team'}))


def test_empty_role_list_denies_every_users_roles(
    reach_topology_instance: TopologyInstance,
) -> None:
    """An empty declared role set denies everyone, including a privileged requester."""
    ti = reach_topology_instance
    locked_lan = ti.get_network('locked-lan')
    assert locked_lan is not None
    assert ti.network_has_reach(locked_lan, frozenset({'blue-team'})) is False
    assert ti.network_has_reach(locked_lan, frozenset()) is False
    assert ti.network_has_reach(locked_lan, UNIVERSAL_ROLES) is False


def test_boolean_never_consults_users_roles(reach_topology_instance: TopologyInstance) -> None:
    """A boolean declaration answers the same regardless of users_roles."""
    ti = reach_topology_instance
    office_lan = ti.get_network('office-lan')
    assert office_lan is not None
    assert ti.network_has_reach(office_lan, frozenset()) is True
    assert ti.network_has_reach(office_lan, frozenset({'anything'})) is True
    assert ti.network_has_reach(office_lan, UNIVERSAL_ROLES) is True


# --- Visibility composition (§2.2) ---------------------------------------------------


def test_one_complete_path_suffices_for_multihomed_host(
    visibility_topology_instance: TopologyInstance,
) -> None:
    """A host with one complete path and one broken path is still visible."""
    ti = visibility_topology_instance
    victim = ti.get_node('victim')
    assert victim in ti.get_visible_hosts(frozenset({'red-team'}))


def test_concealed_router_conceals_what_hangs_beneath_it(
    visibility_topology_instance: TopologyInstance,
) -> None:
    """A hidden router is never visible, and the network mapped only to it is not either."""
    ti = visibility_topology_instance
    router_b = ti.get_node('router-b')
    net_b = ti.get_network('net-b')
    assert router_b not in ti.get_visible_routers(frozenset({'red-team'}))
    assert router_b not in ti.get_visible_routers(UNIVERSAL_ROLES)
    assert net_b not in ti.get_visible_networks(frozenset({'red-team'}))


def test_host_own_denial_cannot_be_overridden(
    visibility_topology_instance: TopologyInstance,
) -> None:
    """A host declaring a role list that denies the requester is never visible."""
    ti = visibility_topology_instance
    victim = ti.get_node('victim')
    assert victim not in ti.get_visible_hosts(frozenset())
    assert victim not in ti.get_visible_hosts(frozenset({'green-team'}))


def test_privileged_user_still_bound_by_boolean_denial(
    visibility_topology_instance: TopologyInstance,
) -> None:
    """Privilege changes only what users_roles holds; a hidden:true router stays hidden."""
    ti = visibility_topology_instance
    router_b = ti.get_node('router-b')
    router_a = ti.get_node('router-a')
    assert router_b not in ti.get_visible_routers(UNIVERSAL_ROLES)
    assert router_a in ti.get_visible_routers(UNIVERSAL_ROLES)


def test_wan_always_visible(visibility_topology_instance: TopologyInstance) -> None:
    """The WAN is always in the visible-network set, for any users_roles."""
    ti = visibility_topology_instance
    assert ti.wan in ti.get_visible_networks(frozenset())
    assert ti.wan in ti.get_visible_networks(UNIVERSAL_ROLES)


# --- Reach composition (§2.3) --------------------------------------------------------


def test_multihomed_machine_reachable_via_one_of_two_networks(
    reach_topology_instance: TopologyInstance,
) -> None:
    """A machine on a role-gated network and a universal one is reachable either way."""
    ti = reach_topology_instance
    office_lan = ti.get_network('office-lan')
    target_lan = ti.get_network('target-lan')
    assert office_lan is not None
    assert target_lan is not None
    assert office_lan in ti.get_user_accessible_hosts_networks(frozenset())
    assert target_lan not in ti.get_user_accessible_hosts_networks(frozenset())
    assert target_lan in ti.get_user_accessible_hosts_networks(frozenset({'blue-team'}))


def test_universally_reachable_network_settles_reach_for_everyone(
    reach_topology_instance: TopologyInstance,
) -> None:
    """accessible_by_user: true on one attachment settles reach regardless of role."""
    ti = reach_topology_instance
    office_lan = ti.get_network('office-lan')
    assert office_lan is not None
    for users_roles in (frozenset(), frozenset({'anyone'}), UNIVERSAL_ROLES):
        assert office_lan in ti.get_user_accessible_hosts_networks(users_roles)


def test_management_network_never_reachable(reach_topology_instance: TopologyInstance) -> None:
    """The management network is not author-declared, so it contributes nothing."""
    ti = reach_topology_instance
    assert ti.man_network not in ti.get_user_accessible_hosts_networks(UNIVERSAL_ROLES)


def test_management_network_never_visible(visibility_topology_instance: TopologyInstance) -> None:
    """The management network is never in the visible-network set, for any users_roles."""
    ti = visibility_topology_instance
    assert ti.man_network not in ti.get_visible_networks(UNIVERSAL_ROLES)


# --- Extremes of users_roles (§2.6) --------------------------------------------------


def test_empty_users_roles_resolves_booleans_unchanged(
    reach_topology_instance: TopologyInstance,
) -> None:
    """An empty users_roles set resolves every boolean exactly as it did before roles."""
    ti = reach_topology_instance
    office_lan = ti.get_network('office-lan')
    assert office_lan is not None
    assert ti.network_has_reach(office_lan, frozenset()) is True


def test_universal_roles_admits_every_non_empty_role_declaration(
    reach_topology_instance: TopologyInstance,
) -> None:
    """UNIVERSAL_ROLES admits any non-empty role declaration."""
    ti = reach_topology_instance
    target_lan = ti.get_network('target-lan')
    assert target_lan is not None
    assert ti.network_has_reach(target_lan, UNIVERSAL_ROLES) is True
