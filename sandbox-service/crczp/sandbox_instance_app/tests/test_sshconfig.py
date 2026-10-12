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

routers:
  - name: gw
    base_box: { image: debian-12-x86_64, mgmt_user: debian }
    flavor: standard.small

networks:
  - name: target-lan
    cidr: 10.10.10.0/24
    accessible_by_roles: [red-team]

net_mappings:
  - host: victim
    network: target-lan
    ip: 10.10.10.5

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

    def test_user_config_lists_main_link_first(self, top_ins_multi_homed):
        """A node's first Host block, which OpenSSH uses, is its first mapping's address."""
        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man

        result = sshconfig.CrczpUserSSHConfig(
            top_ins_multi_homed, UNIVERSAL_ROLES, proxy_jump.Host, 'stack-name'
        )

        assert [host['Host'] for host in result.asdict()[2:]] == [
            'monitoring 10.10.20.6',
            'server-router 10.10.20.1',
            'server 10.10.20.5',
            'monitoring 10.10.40.5',
            'server-router 10.10.40.1',
        ]


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
            if link.network.name == 'target-lan':
                link.ip = '10.10.10.5'
        return ti

    def test_concealed_but_reachable_node_still_gets_an_entry(self):
        """Reach alone decides a node entry; concealment is never consulted."""
        top_ins = self._build_topology_instance()
        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man
        result = sshconfig.CrczpUserSSHConfig(
            top_ins, frozenset({'red-team'}), proxy_jump.Host, 'stack-name'
        )
        assert any('victim' in host.name.split() for host in result.hosts)

    def test_denied_node_receives_no_entry(self):
        """A machine denied reach on every attachment is silently omitted."""
        top_ins = self._build_topology_instance()
        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man
        result = sshconfig.CrczpUserSSHConfig(top_ins, frozenset(), proxy_jump.Host, 'stack-name')
        assert not any('victim' in host.name.split() for host in result.hosts)
