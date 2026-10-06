"""Tests for cleaning up allocation unit resources on the jump proxy host."""

import shlex

import pytest

from crczp.sandbox_instance_app.lib import jump_proxy_cleanup


class TestDeleteJumpSshKey:
    """Tests for the remote home directory removal on the jump proxy."""

    @pytest.fixture
    def ssh(self, mocker):
        """Mock the jump proxy connection; the remote command succeeds silently."""
        ssh = mocker.MagicMock()
        stdout, stderr = mocker.MagicMock(), mocker.MagicMock()
        stderr.read.return_value = b''
        ssh.exec_command.return_value = (mocker.MagicMock(), stdout, stderr)
        mocker.patch.object(jump_proxy_cleanup, 'connect_to_jump', return_value=ssh)
        return ssh

    @staticmethod
    def _allocation_unit(mocker, stack_name):
        unit = mocker.MagicMock()
        unit.get_stack_name.return_value = stack_name
        return unit

    def test_removes_the_stack_home_directory(self, mocker, ssh):
        """Test that the home directory named after the stack is removed."""
        unit = self._allocation_unit(mocker, 'default0-p0000000001-s0000000002')

        jump_proxy_cleanup.delete_jump_ssh_key(unit)

        ssh.exec_command.assert_called_once_with(
            'sudo rm -rf /home/default0-p0000000001-s0000000002'
        )
        ssh.close.assert_called_once()

    def test_shell_metacharacters_in_stack_name_stay_one_argument(self, mocker, ssh):
        """Test that a stack_name_prefix with shell syntax cannot inject a command."""
        unit = self._allocation_unit(mocker, '$(id) x-p0000000001-s0000000002')

        jump_proxy_cleanup.delete_jump_ssh_key(unit)

        command = ssh.exec_command.call_args.args[0]
        assert shlex.split(command) == [
            'sudo',
            'rm',
            '-rf',
            '/home/$(id) x-p0000000001-s0000000002',
        ]
