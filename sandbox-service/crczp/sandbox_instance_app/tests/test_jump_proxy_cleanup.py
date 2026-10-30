"""Tests for cleaning up allocation unit resources on the jump proxy host."""

import shlex

import paramiko
import pytest

from crczp.cloud_commons import StackNameScheme
from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_instance_app.lib import jump_proxy_cleanup

STACK_NAMES = StackNameScheme('a.b')


@pytest.fixture
def ssh(mocker):
    """Mock the jump proxy connection; the remote command succeeds silently."""
    ssh = mocker.MagicMock()
    stdout, stderr = mocker.MagicMock(), mocker.MagicMock()
    stdout.read.return_value = b''
    stdout.channel.recv_exit_status.return_value = 0
    stderr.read.return_value = b''
    ssh.exec_command.return_value = (mocker.MagicMock(), stdout, stderr)
    mocker.patch.object(jump_proxy_cleanup, 'connect_to_jump', return_value=ssh)
    return ssh


def _fail_remote_command(ssh, exit_status: int, error: bytes) -> None:
    _stdin, stdout, stderr = ssh.exec_command.return_value
    stdout.channel.recv_exit_status.return_value = exit_status
    stderr.read.return_value = error


class TestDeleteJumpSshKey:
    """Tests for the remote home directory removal on the jump proxy."""

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

    def test_a_failed_removal_is_raised(self, mocker, ssh):
        """Test that a removal failing on the jump proxy is reported to the caller."""
        _fail_remote_command(ssh, 1, b'rm: cannot remove')

        with pytest.raises(exceptions.NetworkError, match='rm: cannot remove'):
            jump_proxy_cleanup.delete_jump_ssh_key(
                self._allocation_unit(mocker, 'default0-p0000000001-s0000000002')
            )


class TestJumpHomes:
    """Tests for listing and removing stack home directories on the jump proxy."""

    def test_lists_only_homes_matching_the_stack_name_pattern(self, ssh):
        """Test that other users, other prefixes and partial matches are ignored."""
        matching = ['a.b-p0000000001-s0000000002', 'a.b-p0000000010-s0000000003']
        listing = '\n'.join([
            *matching,
            'ubuntu',
            'axb-p0000000001-s0000000002',
            'other-p0000000001-s0000000002',
            'a.b-p0000000001-s0000000002.bak',
            'x-a.b-p0000000001-s0000000002',
            'a.b-p1-s2',
        ])
        ssh.exec_command.return_value[1].read.return_value = listing.encode()

        assert jump_proxy_cleanup.list_jump_homes(STACK_NAMES) == matching
        ssh.exec_command.assert_called_once_with('ls -1 /home')
        ssh.close.assert_called_once()

    def test_lists_every_home_name(self, ssh):
        """Test that the raw listing keeps the names outside the stack name pattern."""
        listing = ['a.b-p0000000001-s0000000002', 'ubuntu', 'a.b-p1-s2']
        ssh.exec_command.return_value[1].read.return_value = '\n'.join(listing).encode()

        assert jump_proxy_cleanup.list_jump_home_names() == listing

    def test_a_failed_listing_raises(self, ssh):
        """Test that a listing failing on the jump proxy is never read as no homes."""
        _fail_remote_command(ssh, 2, b'ls: cannot open directory')

        with pytest.raises(exceptions.NetworkError, match='cannot open directory'):
            jump_proxy_cleanup.list_jump_homes(STACK_NAMES)
        ssh.close.assert_called_once()

    def test_removes_the_given_home_directory(self, ssh):
        """Test that a matching stack name has its home removed through the shell-quoted path."""
        jump_proxy_cleanup.remove_jump_home('a.b-p0000000001-s0000000002', STACK_NAMES)

        ssh.exec_command.assert_called_once_with('sudo rm -rf /home/a.b-p0000000001-s0000000002')
        ssh.close.assert_called_once()

    def test_a_failed_removal_raises(self, ssh):
        """Test that a removal failing on the jump proxy is never reported as done."""
        _fail_remote_command(ssh, 1, b'rm: cannot remove')

        with pytest.raises(exceptions.NetworkError, match='cannot remove'):
            jump_proxy_cleanup.remove_jump_home('a.b-p0000000001-s0000000002', STACK_NAMES)

    @pytest.mark.parametrize(
        'name',
        [
            'ubuntu',
            '../a.b-p0000000001-s0000000002',
            'a.b-p0000000001-s0000000002 /etc',
            'a.b-p0000000001-s0000000002\n',
            'axb-p0000000001-s0000000002',
            '',
            'a.b',
            'a.b-p0000000001',
        ],
    )
    def test_refuses_a_name_outside_the_stack_name_pattern(self, ssh, name):
        """Test that nothing runs, not even a connection, for a foreign name."""
        with pytest.raises(ValueError, match='Not a stack name'):
            jump_proxy_cleanup.remove_jump_home(name, STACK_NAMES)

        ssh.exec_command.assert_not_called()


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
