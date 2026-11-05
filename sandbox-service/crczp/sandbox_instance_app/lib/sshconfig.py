"""SSH configuration classes for CRCZP sandbox access."""

import re
from dataclasses import dataclass, field
from typing import override

import structlog

from crczp.cloud_commons import Link, TopologyInstance, UsersRoles

LOG = structlog.getLogger()

SSH_PROXY_USERNAME = 'user'
SSH_PROXY_KEY = '<path_to_proxy_jump_private_key>'

# OpenSSH separates an option's keyword from its value by whitespace or by `=`.
OPTION_SEPARATOR = re.compile(r'\s*=\s*|\s+')


@dataclass
class SSHHost:
    """One `Host` block of an OpenSSH client config."""

    names: list[str]
    options: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """The block's patterns, space-separated as on its `Host` line."""
        return ' '.join(self.names)

    @override
    def __str__(self) -> str:
        lines = [f'Host {self.name}']
        lines += [f'    {key} {value}' for key, value in self.options.items()]
        return '\n'.join(lines) + '\n'


class CrczpSSHConfig:
    """
    Represents SSH config file for CRCZP purposes.
    This class works in-memory and does not require a backing file.
    """

    def __init__(self) -> None:
        self.hosts: list[SSHHost] = []
        self.raw: str | None = None

    @override
    def __str__(self) -> str:
        return '\n'.join([str(host) for host in self.hosts]) + '\n'

    def serialize(self) -> str:
        """
        Return the string representation of CrczpSSHConfig.
        """
        return str(self)

    def add_host(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        host_name: str,
        user: str,
        identity_file: str,
        proxy_jump: str | None = None,
        alias: str | None = None,
        port: int | None = None,
        **kwargs: str,
    ) -> None:
        """
        Create and add a Host entry to this SSH config file.
        """
        options = {'HostName': host_name, 'User': user}
        if port is not None:
            options['Port'] = str(port)
        options['IdentityFile'] = identity_file
        if proxy_jump:
            options['ProxyJump'] = proxy_jump
        options |= {
            'UserKnownHostsFile': '/dev/null',
            'IdentitiesOnly': 'yes',
            'StrictHostKeyChecking': 'no',
        }
        options |= kwargs
        # Use alias as first name if given, otherwise host_name
        self.hosts.append(SSHHost([alias, host_name] if alias else [host_name], options))

    def add_docker_host(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        host_name: str,
        user: str,
        identity_file: str,
        port: int,
        proxy_jump: str | None = None,
        alias: str | None = None,
        **kwargs: str,
    ) -> None:
        """Create and add a Host entry for a Docker container to this SSH config."""
        self.add_host(
            host_name, user, identity_file, proxy_jump=proxy_jump, alias=alias, port=port, **kwargs
        )

    @classmethod
    def load(cls, file_path: str) -> 'CrczpSSHConfig':
        """Load SSH config from a file path."""
        with open(file_path, encoding='utf-8') as f:
            return cls.from_str(f.read())

    @classmethod
    def from_str(cls, ssh_config: str) -> 'CrczpSSHConfig':
        """
        Load SSH config file from string.

        Options before the first `Host` line apply to every host and are not kept.
        """
        if not ssh_config or not ssh_config.strip():
            raise ValueError('Empty SSHConfig string')
        instance = cls()
        instance.raw = ssh_config
        host: SSHHost | None = None
        for raw_line in ssh_config.splitlines():
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue
            parts = OPTION_SEPARATOR.split(line, maxsplit=1)
            key, value = parts[0], parts[1] if len(parts) > 1 else ''
            if key.lower() == 'host':
                host = SSHHost(value.split())
                instance.hosts.append(host)
            elif host is not None:
                host.options[key] = value
        return instance

    def asdict(self) -> list[dict[str, str]]:
        """
        Return a list of dicts for all hosts.
        """
        return [{'Host': host.name, **host.options} for host in self.hosts]


class CrczpUserSSHConfig(CrczpSSHConfig):
    """
    Represents SSH config file used by CRCZP trainees.
    """

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        top_ins: TopologyInstance,
        users_roles: UsersRoles,
        proxy_host: str,
        proxy_user: str,
        sandbox_private_key_path: str = '<path_to_sandbox_private_key>',
        proxy_port: int = 22,
        role_private_key_paths: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        role_private_key_paths = role_private_key_paths or {}
        # Create an entry for PROXY JUMP host.
        self.add_host(proxy_host, proxy_user, sandbox_private_key_path, port=proxy_port)
        proxy_jump = (
            f'{proxy_user}@{proxy_host}:{proxy_port}'
            if proxy_port != 22
            else f'{proxy_user}@{proxy_host}'
        )
        # Create an entry for MAN as a proxy jump host.
        # TopologyInstance.ip and Link.ip are Optional in crczp.cloud_commons only for the
        # pre-enrichment state; every SSH config here is built from an enriched instance.
        self.add_host(
            top_ins.ip,  # ty: ignore[invalid-argument-type]
            SSH_PROXY_USERNAME,
            sandbox_private_key_path,
            proxy_jump=proxy_jump,
            alias=top_ins.man.name,
        )
        man_proxy_jump = f'{SSH_PROXY_USERNAME}@{top_ins.man.name}'

        # OpenSSH takes the first Host block matching an alias, so a node's main link
        # takes the slot of that node's first entry.
        user_accessible_links = top_ins.get_links_to_user_accessible_nodes(users_roles)
        first_slots: dict[str, int] = {}
        for index, link in enumerate(list(user_accessible_links)):
            first = first_slots.setdefault(link.node.name, index)
            if link is top_ins.get_node_main_link(link.node):
                user_accessible_links[first], user_accessible_links[index] = (
                    link,
                    user_accessible_links[first],
                )
        # Create an entry for user-accessible nodes of a sandbox. A node carrying no
        # accessible_by_roles metadata, or one whose declared roles none of the held
        # keys match, is unlocked by the flat key; one with a match is unlocked by the
        # lexicographically smallest matching role's key.
        for link in user_accessible_links:
            node_roles = top_ins.get_accessible_by_roles_for_node(link.node)
            matching_roles = set(node_roles or ()) & role_private_key_paths.keys()
            identity_file = (
                role_private_key_paths[min(matching_roles)]
                if matching_roles
                else sandbox_private_key_path
            )
            self.add_host(
                link.ip,  # ty: ignore[invalid-argument-type]
                SSH_PROXY_USERNAME,
                identity_file,
                proxy_jump=man_proxy_jump,
                alias=link.node.name,
            )

        # Create entries for docker containers
        if hasattr(top_ins, 'containers') and top_ins.containers:
            for container_mapping in top_ins.containers.container_mappings:
                self.add_docker_host(
                    '127.0.0.1',
                    'root',
                    sandbox_private_key_path,
                    port=container_mapping.port,
                    proxy_jump=container_mapping.host,
                    alias=f'{container_mapping.host}-{container_mapping.container}',
                )


class CrczpMgmtSSHConfig(CrczpSSHConfig):
    """
    Represents SSH config file used by CRCZP designers/organizers.
    """

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        top_ins: TopologyInstance,
        proxy_host: str,
        proxy_user: str,
        proxy_port: int = 22,
        pool_private_key_path: str = '<path_to_pool_private_key>',
        proxy_private_key_path: str = '<path_to_pool_private_key>',
    ) -> None:
        super().__init__()
        # Create an entry for PROXY JUMP host.
        self.add_host(proxy_host, proxy_user, proxy_private_key_path, port=proxy_port)
        proxy_jump = (
            f'{proxy_user}@{proxy_host}:{proxy_port}'
            if proxy_port != 22
            else f'{proxy_user}@{proxy_host}'
        )
        # Create an entry for MAN as a proxy jump host.
        # TopologyInstance.ip and Link.ip are Optional in crczp.cloud_commons only for the
        # pre-enrichment state; every SSH config here is built from an enriched instance.
        self.add_host(
            top_ins.ip,  # ty: ignore[invalid-argument-type]
            top_ins.man.base_box.mgmt_user,
            pool_private_key_path,
            proxy_jump=proxy_jump,
            alias=top_ins.man.name,
        )
        man_proxy_jump = f'{top_ins.man.base_box.mgmt_user}@{top_ins.man.name}'

        # Create an entry for every other node of a sandbox.
        for link in self._get_man_accessible_node_links(top_ins):
            self.add_host(
                link.ip,  # ty: ignore[invalid-argument-type]
                link.node.base_box.mgmt_user,
                pool_private_key_path,
                proxy_jump=man_proxy_jump,
                alias=link.node.name,
            )

    @classmethod
    def _get_man_accessible_node_links(cls, top_ins: TopologyInstance) -> list[Link]:
        """
        Get links for MAN-accessible nodes using Management network.
        """
        return [
            link_pair.second
            for link_pair in top_ins.get_link_pairs_man_to_nodes_over_management_network()
        ]


class CrczpAnsibleSSHConfig(CrczpMgmtSSHConfig):
    """
    Represents SSH config file used by CRCZP automated provisioning using Ansible.
    """

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        top_ins: TopologyInstance,
        pool_private_key_path: str,
        proxy_host: str,
        proxy_user: str,
        proxy_private_key_path: str | None,
        proxy_port: int = 22,
    ) -> None:
        super().__init__(
            top_ins=top_ins,
            proxy_host=proxy_host,
            proxy_user=proxy_user,
            proxy_port=proxy_port,
            pool_private_key_path=pool_private_key_path,
            proxy_private_key_path=proxy_private_key_path or '<path_to_pool_private_key>',
        )


class CrczpAnsibleCleanupSSHConfig(CrczpSSHConfig):
    """
    Represents SSH config file used by CRCZP AnsibleCleanupStage.
    """

    def __init__(
        self,
        proxy_jump_host: str,
        proxy_jump_user: str,
        pool_private_key_path: str,
        proxy_port: int = 22,
    ) -> None:
        super().__init__()
        self.add_host(proxy_jump_host, proxy_jump_user, pool_private_key_path, port=proxy_port)
