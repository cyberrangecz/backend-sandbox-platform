"""Tests for SSH config generation utilities."""

from django.conf import settings

from crczp.sandbox_instance_app.lib import sshconfig


class TestGetSshConfig:
    """Tests for CrczpSSHConfig generation methods."""

    def test_create_user_config_success(self, top_ins, user_ssh_config):
        """Test successful creation of user SSH config from a topology instance."""
        proxy_jump = settings.CRCZP_CONFIG.proxy_jump_to_man
        result = sshconfig.CrczpUserSSHConfig(top_ins, proxy_jump.Host, 'stack-name')
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

        result = sshconfig.CrczpUserSSHConfig(top_ins_multi_homed, proxy_jump.Host, 'stack-name')

        assert [host['Host'] for host in result.asdict()[2:]] == [
            'monitoring 10.10.20.6',
            'server-router 10.10.20.1',
            'server 10.10.20.5',
            'monitoring 10.10.40.5',
            'server-router 10.10.40.1',
        ]
