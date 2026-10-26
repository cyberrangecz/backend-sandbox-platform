"""Utilities for cleaning up resources on the jump proxy host."""

import shlex
import time

import paramiko
import structlog
from django.conf import settings
from paramiko import PKey, SSHClient

from crczp.cloud_commons import StackNameScheme
from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_instance_app.models import SandboxAllocationUnit

LOG = structlog.get_logger()

# The jump host refuses or drops connections while it is overloaded, e.g. when the
# cleanup of a whole pool removes many keys at once.
CONNECT_ATTEMPTS = 5
CONNECT_RETRY_DELAY_SECONDS = 1


def delete_jump_ssh_key(allocation_unit: SandboxAllocationUnit) -> None:
    """
    Delete the home directory of the allocation unit's stack, with its SSH key, from the jump proxy.

    :raise NetworkError: The jump proxy cannot be reached or the removal fails on it
    """
    stack_name = allocation_unit.get_stack_name()
    try:
        error = _remove_home(stack_name)
    except (OSError, paramiko.SSHException, ValueError) as exc:
        error = str(exc) or type(exc).__name__
    if error:
        raise exceptions.NetworkError(
            f'Failed to remove the home of {stack_name} from proxy jump: {error}'
        )


def list_jump_homes(stack_names: StackNameScheme) -> list[str]:
    """
    List the home directory names on the jump proxy that are stack names of one deployment.

    :param stack_names: The stack names of the deployment
    :return: The full stack names that have a home directory
    :raise NetworkError: The listing fails on the jump proxy
    """
    return stack_names.select_stack_names(list_jump_home_names(), 'the jump host homes')


def list_jump_home_names() -> list[str]:
    """
    List the name of every home directory on the jump proxy.

    :raise NetworkError: The listing fails on the jump proxy
    """
    exit_status, listing, error = _run_on_jump('ls -1 /home')
    if exit_status:
        raise exceptions.NetworkError(
            f'Failed to list homes on proxy jump (exit status {exit_status}): {error}'
        )
    return listing.splitlines()


def remove_jump_home(stack_name: str, stack_names: StackNameScheme) -> None:
    """
    Remove the home directory of one stack from the jump proxy.

    :param stack_name: The full stack name
    :param stack_names: The stack names of the deployment
    :raise ValueError: The name is not a full stack name of the deployment; nothing connects
    :raise NetworkError: The removal fails on the jump proxy
    """
    stack_names.require(stack_name)
    error = _remove_home(stack_name)
    if error:
        raise exceptions.NetworkError(
            f'Failed to remove the home of {stack_name} from proxy jump: {error}'
        )


def _remove_home(stack_name: str) -> str:
    """Remove the home directory of a stack, returning the error of a failed removal or ''."""
    # The command runs through the jump host's shell as root, so the path is shell-quoted.
    home_dir = shlex.quote(f'/home/{stack_name}')
    exit_status, _output, error = _run_on_jump(f'sudo rm -rf {home_dir}')
    if exit_status:
        return error or f'exit status {exit_status}'
    return ''


def _run_on_jump(command: str) -> tuple[int, str, str]:
    """Run a command on the jump proxy until it ends; returns its exit status, output and errors."""
    ssh = connect_to_jump_with_retry()
    try:
        _stdin, stdout, stderr = ssh.exec_command(command)
        output = stdout.read().decode()
        error = stderr.read().decode()
        return stdout.channel.recv_exit_status(), output, error
    finally:
        ssh.close()


def connect_to_jump_with_retry() -> SSHClient:
    """Connect to the jump proxy, retrying connection-level failures.

    Only OSError is retried (refused or reset connections, timeouts). Authentication and
    host-key errors are paramiko.SSHException and fail on the first attempt.
    """
    attempt = 1
    while True:
        try:
            return connect_to_jump()
        except OSError as exc:
            if attempt >= CONNECT_ATTEMPTS:
                LOG.warning('jump_proxy_connection_failed', attempts=attempt, error=str(exc))
                raise
            LOG.warning('jump_proxy_connection_retry', attempt=attempt, error=str(exc))
            time.sleep(CONNECT_RETRY_DELAY_SECONDS)
            attempt += 1


def connect_to_jump() -> SSHClient:
    """Establish an SSH connection to the configured jump proxy host."""
    hostname = settings.CRCZP_CONFIG.proxy_jump_to_man.Host
    user = settings.CRCZP_CONFIG.proxy_jump_to_man.User
    identity_file = settings.CRCZP_CONFIG.proxy_jump_to_man.IdentityFile
    port = settings.CRCZP_CONFIG.proxy_jump_to_man.Port

    return ssh_connect(hostname, port, user, identity_file)


def ssh_connect(hostname: str, port: int, username: str, key_file_path: str) -> SSHClient:
    """Create and return an authenticated SSH connection."""
    ssh = paramiko.SSHClient()
    try:
        ssh.load_system_host_keys()
        # Accepts an unknown jump host key; it is verified only if it is in known_hosts.
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # noqa: S507
        private_key = load_private_key(key_file_path)

        ssh.connect(hostname, port=port, username=username, pkey=private_key)
        return ssh
    except Exception as e:
        # A failed connect can leave the transport thread and socket open.
        ssh.close()
        LOG.warning(f'Failed to connect to {hostname}: {e}')
        raise


def load_private_key(key_path: str) -> PKey:
    """Load and return a Paramiko private key from the given file path."""
    key_classes: list[type[PKey]] = [paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key]
    for key_class in key_classes:
        try:
            return key_class.from_private_key_file(key_path)
        except (OSError, paramiko.SSHException):
            continue
    raise ValueError('Could not load private key. Unsupported key type or file not found.')
