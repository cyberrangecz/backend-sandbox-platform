"""Tests for SSH config generation utilities."""

from django.conf import settings

from crczp.cloud_commons import UNIVERSAL_ROLES, TopologyInstance, TransformationConfiguration
from crczp.sandbox_instance_app.lib import sshconfig
from crczp.topology_definition.models import TopologyDefinition

ROLE_GATED_SSH_DEFINITION = """
name: sshconfig-roles-sandbox
hosts:
  - name: victim
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small
    visible_by_roles: []

  - name: plain
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small

routers:
  - name: gw
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small

networks:
  - name: target-lan
    cidr: 10.10.10.0/24
    accessible_by_roles: [red-team]

  - name: office-lan
    cidr: 10.10.11.0/24

net_mappings:
  - host: victim
    network: target-lan
    ip: 10.10.10.5

  - host: plain
    network: office-lan
    ip: 10.10.11.5

router_mappings:
  - router: gw
    network: target-lan
    ip: 10.10.10.1

groups: []
"""


class TestGetSshConfig:
    """Tests for CrczpSSHConfig generation methods."""

    def test_create_user_config_success(self, top_ins, user_ssh_config):
        """Test successful creation of user SSH config from a topology instance."""
        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man
        result = sshconfig.CrczpUserSSHConfig(
            top_ins, UNIVERSAL_ROLES, proxy_jump.Host, 'stack-name'
        )
        assert result.asdict() == user_ssh_config.asdict()

    def test_create_management_config_success(self, top_ins, management_ssh_config):
        """Test successful creation of management SSH config."""
        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man
        result = sshconfig.CrczpMgmtSSHConfig(top_ins, proxy_jump.Host, 'pool-prefix')
        assert result.asdict() == management_ssh_config.asdict()

    def test_create_ansible_config_success(self, top_ins, ansible_ssh_config):
        """Test successful creation of ansible SSH config."""
        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man
        result = sshconfig.CrczpAnsibleSSHConfig(
            top_ins,
            '/root/.ssh/pool_mng_key',
            proxy_jump.Host,
            proxy_jump.User,
            '/root/.ssh/id_rsa',
        )
        assert result.asdict() == ansible_ssh_config.asdict()

    def test_password_host_prefers_key_with_password_fallback(self, top_ins):
        """A host with a management password keeps the key and adds a password fallback."""
        server_def = next(h for h in top_ins.topology_definition.hosts if h.name == 'server')
        server_def.base_box.mgmt_password = 'inv3a-t3ch'  # nosec B105

        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man
        result = sshconfig.CrczpMgmtSSHConfig(top_ins, proxy_jump.Host, 'pool-prefix')

        def first_name(host: object) -> str:
            return host[0] if isinstance(host, (list, tuple)) else str(host).split()[0]

        server_entry = next(e for e in result.asdict() if first_name(e['Host']) == 'server')
        assert 'IdentityFile' in server_entry
        assert server_entry.get('IdentitiesOnly')  # truthy (yes)
        assert server_entry.get('PreferredAuthentications') == 'publickey,password'
        assert server_entry.get('PubkeyAuthentication') != 'no'

        # The directives must reach the serialized config that SSH actually reads.
        serialized = result.serialize()
        assert 'PreferredAuthentications publickey,password' in serialized
        assert 'IdentityFile' in serialized

        # A host without a password is untouched (key-based, no password fallback).
        home_entry = next(e for e in result.asdict() if first_name(e['Host']) == 'home')
        assert 'IdentityFile' in home_entry
        assert home_entry.get('IdentitiesOnly')  # truthy (yes)
        assert 'PreferredAuthentications' not in home_entry


class TestRoleAwareUserSshConfig:
    """Tests for reach-gated SSH entries, independent of visibility."""

    @staticmethod
    def _build_topology_instance() -> TopologyInstance:
        topology_definition = TopologyDefinition.load(ROLE_GATED_SSH_DEFINITION)
        trc_config = TransformationConfiguration(
            man_image='debian-12-x86_64', man_flavor='standard.small', man_user='debian'
        )
        ti = TopologyInstance(topology_definition, trc_config)
        ti.ip = '10.10.10.10'
        for link in ti.get_links():
            if link.network.name == 'target-lan' and link.node.name == 'victim':
                link.ip = '10.10.10.5'
            elif link.network.name == 'office-lan' and link.node.name == 'plain':
                link.ip = '10.10.11.5'
        return ti

    def test_concealed_but_reachable_node_still_gets_an_entry(self):
        """Reach alone decides a node entry; concealment is never consulted."""
        top_ins = self._build_topology_instance()
        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man
        result = sshconfig.CrczpUserSSHConfig(
            top_ins,
            frozenset({'red-team'}),
            proxy_jump.Host,
            'stack-name',
            role_private_key_paths={'red-team': '~/.ssh/red-team-key'},
        )
        assert any('victim' in host.name.split() for host in result.hosts)

    def test_denied_node_receives_no_entry(self):
        """A machine denied reach on every attachment is silently omitted."""
        top_ins = self._build_topology_instance()
        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man
        result = sshconfig.CrczpUserSSHConfig(top_ins, frozenset(), proxy_jump.Host, 'stack-name')
        assert not any('victim' in host.name.split() for host in result.hosts)

    def test_role_gated_node_points_at_its_role_key(self):
        """A node whose reach is role-gated uses that role's private-key path."""
        top_ins = self._build_topology_instance()
        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man
        result = sshconfig.CrczpUserSSHConfig(
            top_ins,
            frozenset({'red-team'}),
            proxy_jump.Host,
            'stack-name',
            sandbox_private_key_path='~/.ssh/flat-key',
            role_private_key_paths={'red-team': '~/.ssh/red-team-key'},
        )
        victim = next(host for host in result.hosts if 'victim' in host.name.split())
        assert victim.attributes()['IdentityFile'] == '~/.ssh/red-team-key'

    def test_role_free_node_points_at_the_flat_key(self):
        """A node carrying no accessible_by_roles metadata uses the flat key unconditionally."""
        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man
        top_ins = self._build_topology_instance()
        result = sshconfig.CrczpUserSSHConfig(
            top_ins,
            UNIVERSAL_ROLES,
            proxy_jump.Host,
            'stack-name',
            sandbox_private_key_path='~/.ssh/flat-key',
            role_private_key_paths={'red-team': '~/.ssh/red-team-key'},
        )
        plain = next(host for host in result.hosts if 'plain' in host.name.split())
        assert plain.attributes()['IdentityFile'] == '~/.ssh/flat-key'

    def test_multi_role_holder_picks_lexicographically_smallest_matching_role(self):
        """A node admitting several of the requester's held roles picks the smallest name."""
        definition = ROLE_GATED_SSH_DEFINITION.replace(
            'accessible_by_roles: [red-team]', 'accessible_by_roles: [red-team, observer]'
        )
        topology_definition = TopologyDefinition.load(definition)
        trc_config = TransformationConfiguration(
            man_image='debian-12-x86_64', man_flavor='standard.small', man_user='debian'
        )
        top_ins = TopologyInstance(topology_definition, trc_config)
        top_ins.ip = '10.10.10.10'
        for link in top_ins.get_links():
            if link.network.name == 'target-lan':
                link.ip = '10.10.10.5'

        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man
        result = sshconfig.CrczpUserSSHConfig(
            top_ins,
            frozenset({'red-team', 'observer'}),
            proxy_jump.Host,
            'stack-name',
            role_private_key_paths={
                'red-team': '~/.ssh/red-team-key',
                'observer': '~/.ssh/observer-key',
            },
        )
        victim = next(host for host in result.hosts if 'victim' in host.name.split())
        assert victim.attributes()['IdentityFile'] == '~/.ssh/observer-key'
