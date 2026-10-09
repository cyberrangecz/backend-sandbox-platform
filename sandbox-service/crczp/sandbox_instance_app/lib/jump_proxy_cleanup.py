"""Utilities for cleaning up resources on the jump proxy host."""

import shlex
import time

import paramiko
import structlog
from django.conf import settings
from paramiko import PKey, SSHClient

from crczp.sandbox_instance_app.models import SandboxAllocationUnit

LOG = structlog.get_logger()

# The jump host refuses or drops connections while it is overloaded, e.g. when the
# cleanup of a whole pool removes many keys at once.
CONNECT_ATTEMPTS = 5
CONNECT_RETRY_DELAY_SECONDS = 1


def delete_jump_ssh_key(allocation_unit: SandboxAllocationUnit) -> None:
    """Delete the SSH key for the given allocation unit from the jump proxy."""
    name = allocation_unit.get_stack_name()
    ssh = connect_to_jump_with_retry()
    try:
        # The command runs through the jump host's shell as root, and the stack name
        # starts with the configured stack_name_prefix, which is only length-checked.
        home_dir = shlex.quote(f'/home/{name}')
        _stdin, stdout, stderr = ssh.exec_command(f'sudo rm -rf {home_dir}')

        # Wait for the command to finish
        stdout.channel.recv_exit_status()
        error = stderr.read().decode()
        if error:
            LOG.warning(f'Failed to delete key for {name} from proxy jump: {error}')
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
