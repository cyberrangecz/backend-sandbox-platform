"""Tests for cleaning up allocation unit resources on the jump proxy host."""

import shlex

import paramiko
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


class TestConnectToJumpWithRetry:
    """Tests for retrying the jump proxy connection."""

    @pytest.fixture
    def sleep(self, mocker):
        """Do not actually wait between attempts."""
        return mocker.patch.object(jump_proxy_cleanup.time, 'sleep')

    def test_retries_connection_errors_then_connects(self, mocker, sleep):
        """Test that refused connections are retried until the jump host answers."""
        ssh = mocker.MagicMock()
        refused = paramiko.ssh_exception.NoValidConnectionsError({
            ('127.0.0.1', 22): ConnectionRefusedError()
        })
        connect = mocker.patch.object(
            jump_proxy_cleanup, 'connect_to_jump', side_effect=[refused, TimeoutError(), ssh]
        )

        assert jump_proxy_cleanup.connect_to_jump_with_retry() is ssh
        assert connect.call_count == 3
        assert sleep.call_count == 2

    def test_gives_up_after_the_last_attempt(self, mocker, sleep):
        """Test that the last connection error is raised once every attempt failed."""
        errors = [OSError(f'attempt {i}') for i in range(jump_proxy_cleanup.CONNECT_ATTEMPTS)]
        connect = mocker.patch.object(jump_proxy_cleanup, 'connect_to_jump', side_effect=errors)

        with pytest.raises(OSError, match='attempt 4'):
            jump_proxy_cleanup.connect_to_jump_with_retry()
        assert connect.call_count == jump_proxy_cleanup.CONNECT_ATTEMPTS
        assert sleep.call_count == jump_proxy_cleanup.CONNECT_ATTEMPTS - 1

    def test_authentication_error_is_not_retried(self, mocker, sleep):
        """Test that a rejected key fails at once instead of hammering the jump host."""
        connect = mocker.patch.object(
            jump_proxy_cleanup,
            'connect_to_jump',
            side_effect=paramiko.AuthenticationException('rejected'),
        )

        with pytest.raises(paramiko.AuthenticationException):
            jump_proxy_cleanup.connect_to_jump_with_retry()
        assert connect.call_count == 1
        sleep.assert_not_called()


def test_ssh_connect_closes_the_client_when_connecting_fails(mocker):
    """Test that a failed connection attempt does not leak the SSH client."""
    client = mocker.MagicMock()
    client.connect.side_effect = TimeoutError()
    mocker.patch.object(jump_proxy_cleanup.paramiko, 'SSHClient', return_value=client)
    mocker.patch.object(jump_proxy_cleanup, 'load_private_key')

    with pytest.raises(TimeoutError):
        jump_proxy_cleanup.ssh_connect('jump', 22, 'user', '/key')
    client.close.assert_called_once()
