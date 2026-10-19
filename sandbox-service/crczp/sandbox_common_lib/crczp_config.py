"""
Django Apps configuration file
"""

import os
from collections.abc import Callable
from enum import Enum
from typing import Annotated, Any

from pydantic import BeforeValidator, Field, PlainSerializer, field_validator
from ruamel.yaml.nodes import MappingNode

from crczp.cloud_commons import TransformationConfiguration
from crczp.sandbox_common_lib import crczp_config_validation
from crczp.topology_definition.yaml_model import (
    YamlBool,
    YamlInt,
    YamlModel,
    YamlModelError,
    YamlStr,
    positional_init,
)

HEAD_IP = '0.0.0.0'  # noqa: S104  # default head host address, not a socket bind
LOG_FILE = 'sandbox-service.log'
LOG_LEVEL = 'INFO'
# Placeholder; the real token is set as git_access_token in config.yml.
GIT_TOKEN = '<default_token>'  # noqa: S105
GIT_SERVER = 'gitlab.com'
GIT_SSH_PORT = 22
GIT_REST_SERVER = 'https://gitlab.com/'
GIT_USER = 'git'
GIT_PRIVATE_KEY = os.path.expanduser('~/.ssh/git_rsa_key')
ANSIBLE_NETWORKING_REV = 'master'
SANDBOX_BUILD_TIMEOUT = 3600 * 2
SANDBOX_DELETE_TIMEOUT = 3600
CLEANUP_RETRY_LIMIT = 3
CLEANUP_RETRY_DELAY = 60
SANDBOX_ANSIBLE_TIMEOUT = 3600 * 2
VOLUMES_PATH = '/tmp/crczp'  # noqa: S108  # default for volumes_path in config.yml, not a temp file
PERSISTENT_VOLUME_CLAIM_NAME = 'sandbox-service'
ANSIBLE_DOCKER_IMAGE = 'ghcr.io/cyberrangecz/crczp-ansible-runner:1.3.0'
ANSIBLE_DOCKER_NETWORK = 'bridge'
ANSWERS_STORAGE_API = 'http://answers-storage:8087/answers-storage/api/v1'
SSL_CA_CERTIFICATE_VERIFY = '/etc/ssl/certs'
TRAINEE_SANDBOX_CLEANUP_MAX_AGE_HOURS = 24
LOST_JOB_OBSERVER_GRACE_SECONDS = 900
DATABASE_ENGINE = 'django.db.backends.postgresql'
DATABASE_HOST = 'localhost'
DATABASE_NAME = 'postgres'
# Local-development default; deployments set database.password in config.yml.
DATABASE_PASSWORD = 'postgres'  # noqa: S105
DATABASE_PORT = '5432'
DATABASE_USER = 'postgres'
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_DB = 0
REDIS_TIMEOUT = 86400 * 30

LEGACY_OPENSTACK_KEYS = {
    f'os_{name}': name
    for name in (
        'auth_url',
        'application_credential_id',
        'application_credential_secret',
        'console_type',
    )
}

# Enums are written by member name.
BY_NAME = PlainSerializer(lambda member: member.name, when_used='json')


def _parsed_by[E: Enum](enum: type[E], create: Callable[[str], E]) -> BeforeValidator:
    """
    Parse a config value into enum with create, which raises ValueError for an unknown one.
    """
    return BeforeValidator(lambda value: value if isinstance(value, enum) else create(str(value)))


def _by_name[E: Enum](enum: type[E], setting: str) -> Callable[[str], E]:
    """
    Look an enum member up by its exact name, naming the setting and the valid names if absent.
    """

    def create(value: str) -> E:
        try:
            return enum[value]
        except KeyError:
            valid = ', '.join(member.name for member in enum)
            raise ValueError(
                f'Invalid value for {setting}: {value}. Expected one of: {valid}.'
            ) from None

    return create


def _default_if_null(model: Callable[[], YamlModel]) -> BeforeValidator:
    """
    Read an explicit null like an absent block: as a fresh default instance.
    """
    return BeforeValidator(lambda value: model() if value is None else value)


class ProxyJump(YamlModel):
    """SSH ProxyJump configuration for connecting to managed nodes."""

    # pylint: disable=invalid-name
    Host: YamlStr
    User: YamlStr
    Port: YamlInt = 22
    IdentityFile: YamlStr

    @positional_init
    def __init__(
        self, host: str, user: str, identity_file: str, port: int = 22, **data: Any
    ) -> None:
        fields: dict[str, Any] = {
            'Host': host,
            'User': user,
            'IdentityFile': identity_file,
            'Port': port,
            **data,
        }
        super().__init__(**fields)


class TerraformConfiguration(YamlModel):
    """Terraform backend configuration settings."""

    backend_type: YamlStr


class AnsibleRunnerSettings(YamlModel):
    """Settings for the Ansible runner backend (Docker or Kubernetes)."""

    backend: YamlStr = 'docker'
    namespace: YamlStr = 'crczp'
    volumes_path: YamlStr = VOLUMES_PATH
    persistent_volume_claim_name: YamlStr = PERSISTENT_VOLUME_CLAIM_NAME


class TraineeSandboxCleanupConfiguration(YamlModel):
    """Cleanup of the sandboxes trainees allocated for themselves.

    The cleanup_trainee_sandboxes management command does the cleanup; the deployment runs
    it periodically, e.g. as a Kubernetes CronJob.
    """

    enabled: YamlBool = False
    # How long a trainee keeps their sandbox before the cleanup removes it.
    max_age_hours: YamlInt = TRAINEE_SANDBOX_CLEANUP_MAX_AGE_HOURS

    @field_validator('max_age_hours')
    @classmethod
    def _valid_max_age_hours(cls, max_age_hours: int) -> int:
        crczp_config_validation.validate_trainee_cleanup_max_age_hours(cls, max_age_hours)
        return max_age_hours


class LostJobObserverConfiguration(YamlModel):
    """Detection of sandbox work whose RQ job was lost.

    The observe_lost_jobs management command does the detection; the deployment runs it
    periodically, e.g. as a Kubernetes CronJob.
    """

    enabled: YamlBool = True
    # How long a job that is dead but still in Redis may wait for RQ to collect it.
    grace_seconds: YamlInt = LOST_JOB_OBSERVER_GRACE_SECONDS

    @field_validator('grace_seconds')
    @classmethod
    def _valid_grace_seconds(cls, grace_seconds: int) -> int:
        crczp_config_validation.validate_lost_job_observer_grace_seconds(cls, grace_seconds)
        return grace_seconds


class Database(YamlModel):
    """Database connection configuration."""

    engine: YamlStr = DATABASE_ENGINE
    host: YamlStr = DATABASE_HOST
    name: YamlStr = DATABASE_NAME
    password: YamlStr = DATABASE_PASSWORD
    port: YamlStr = DATABASE_PORT
    user: YamlStr = DATABASE_USER


class Redis(YamlModel):
    """Redis connection and cache timeout configuration."""

    host: YamlStr = REDIS_HOST
    port: YamlInt = REDIS_PORT
    db: YamlInt = REDIS_DB
    default_cache_timeout: YamlInt = REDIS_TIMEOUT
    uag_cache_timeout: YamlInt = REDIS_TIMEOUT
    topology_cache_timeout: YamlInt = REDIS_TIMEOUT


class GitType(Enum):
    """Supported Git provider types."""

    GITLAB = 1
    INTERNAL = 2  # DEPRECATED
    GITHUB = 3


class OpenStackConsoleType(Enum):
    """Supported OpenStack console types."""

    NOVNC = 'novnc'
    XVPVNC = 'xvpvnc'
    SPICE_HTML5 = 'spice-html5'
    RDP_HTML5 = 'rdp-html5'
    SERIAL = 'serial'
    WEBMKS = 'webmks'

    @classmethod
    def create(cls, value: str) -> 'OpenStackConsoleType':
        """Create an OpenStackConsoleType from a string value."""
        try:
            return cls[value.upper().replace('-', '_')]
        except KeyError:
            valid = ', '.join(console.value for console in cls)
            raise ValueError(
                f'Invalid value for console_type: {value}. Expected one of: {valid}.'
            ) from None


class AwsConfiguration(YamlModel):
    """AWS cloud provider configuration."""

    access_key_id: YamlStr = ''
    secret_access_key: YamlStr = ''
    region: YamlStr = ''
    availability_zone: YamlStr = ''
    base_vpc: YamlStr = 'Base VPC'
    base_subnet: YamlStr = 'Base Subnet'


class OpenStackConfiguration(YamlModel):
    """OpenStack cloud provider configuration."""

    auth_url: YamlStr | None = None
    application_credential_id: YamlStr | None = None
    application_credential_secret: YamlStr | None = None
    console_type: Annotated[
        OpenStackConsoleType,
        _parsed_by(OpenStackConsoleType, OpenStackConsoleType.create),
        BY_NAME,
    ] = OpenStackConsoleType.SPICE_HTML5


class NamingStrategy(YamlModel):
    """Naming strategy for OpenStack resource names."""

    pattern: YamlStr
    replace: YamlStr = ''


# Sandbox flavor key to OpenStack flavor name.
FlavorMapping = dict[YamlStr, YamlStr]
# Git server base URL to access token.
GitProviders = dict[YamlStr, YamlStr]


class SMTPEncryption(Enum):
    """Supported SMTP encryption modes."""

    TSL = 1
    SSL = 2
    INSECURE = 3


class NetbirdConfiguration(YamlModel):
    """NetBird VPN management configuration."""

    management_url: YamlStr
    client_management_url: YamlStr | None = None
    # Path to a file containing the service user PAT. The file is read fresh on
    # every Netbird client creation so a rotated secret (e.g. a volume-mounted
    # Kubernetes Secret) is picked up without restarting the service.
    service_user_pat_file: YamlStr
    key_expiry_seconds: YamlInt = 1209600
    # Upper bound (seconds) on the time spent tearing down a single sandbox's
    # Netbird resources during cleanup. Teardown runs synchronously in the
    # request thread, so a slow or unreachable Netbird endpoint must not block
    # the cleanup path indefinitely; once the budget is exceeded the remaining
    # resources are left behind (best-effort teardown).
    teardown_budget_seconds: YamlInt = 60

    @field_validator('key_expiry_seconds')
    @classmethod
    def _valid_key_expiry(cls, key_expiry_seconds: int) -> int:
        crczp_config_validation.validate_netbird_key_expiry(cls, key_expiry_seconds)
        return key_expiry_seconds

    @field_validator('teardown_budget_seconds')
    @classmethod
    def _valid_teardown_budget(cls, budget_seconds: int) -> int:
        crczp_config_validation.validate_netbird_teardown_budget_seconds(cls, budget_seconds)
        return budget_seconds


class TopologyCacheMode(Enum):
    """How GitHub sandbox-definition topology is cached (GitHub provider only)."""

    AGGRESSIVE = 1  # cache key stable per branch; topology served until TTL expires
    FRESH = 2  # resolve branch HEAD to commit SHA; cache key tracks the branch
    FRESH_IMPORT = 3  # branch-keyed cache, but (re)import bypasses the cache and refreshes it

    @classmethod
    def create(cls, value: str) -> 'TopologyCacheMode':
        """Create a TopologyCacheMode from a string value."""
        try:
            return cls[value.upper().replace('-', '_')]
        except KeyError:
            valid = ', '.join(mode.name for mode in cls)
            raise ValueError(
                f'Invalid value for topology_cache_mode: {value}. Expected one of: {valid}.'
            ) from None


class CrczpConfiguration(YamlModel):
    """Top-level CRCZP application configuration loaded from a YAML file."""

    head_host: YamlStr = HEAD_IP
    syslog_destination_port: YamlInt = 515

    # Absent or null, the block takes its defaults, a fresh instance per load.
    openstack: Annotated[OpenStackConfiguration, _default_if_null(OpenStackConfiguration)] = Field(
        default_factory=OpenStackConfiguration
    )
    aws: AwsConfiguration | None = None

    log_file: YamlStr = LOG_FILE
    log_level: YamlStr = LOG_LEVEL

    man_port: YamlInt = 4822

    # Sandbox creation configuration
    git_user: YamlStr = GIT_USER
    git_providers: GitProviders = Field(default_factory=dict)

    # Deprecated git values
    git_access_token: YamlStr = GIT_TOKEN
    git_server: YamlStr = GIT_SERVER
    git_ssh_port: YamlInt = GIT_SSH_PORT
    git_rest_server: YamlStr = GIT_REST_SERVER
    git_private_key: YamlStr = GIT_PRIVATE_KEY
    git_type: Annotated[GitType, _parsed_by(GitType, _by_name(GitType, 'git_type')), BY_NAME] = (
        GitType.GITLAB
    )

    git_skip_ssl_verification: YamlBool = False

    topology_cache_mode: Annotated[
        TopologyCacheMode, _parsed_by(TopologyCacheMode, TopologyCacheMode.create), BY_NAME
    ] = TopologyCacheMode.AGGRESSIVE

    ansible_networking_url: YamlStr
    ansible_networking_rev: YamlStr = ANSIBLE_NETWORKING_REV

    image_naming_strategy: NamingStrategy | None = None
    flavor_mapping: FlavorMapping | None = None

    proxy_jump_to_man: ProxyJump
    terraform_configuration: TerraformConfiguration
    database: Database
    redis: Redis = Field(default_factory=Redis)

    sandbox_build_timeout: YamlInt = SANDBOX_BUILD_TIMEOUT
    sandbox_delete_timeout: YamlInt = SANDBOX_DELETE_TIMEOUT
    cleanup_retry_limit: YamlInt = CLEANUP_RETRY_LIMIT
    cleanup_retry_delay: YamlInt = CLEANUP_RETRY_DELAY
    sandbox_ansible_timeout: YamlInt = SANDBOX_ANSIBLE_TIMEOUT

    ansible_docker_image: YamlStr = ANSIBLE_DOCKER_IMAGE
    ansible_docker_network: YamlStr = ANSIBLE_DOCKER_NETWORK

    ansible_runner_settings: AnsibleRunnerSettings = Field(default_factory=AnsibleRunnerSettings)

    answers_storage_api: YamlStr = ANSWERS_STORAGE_API

    ssl_ca_certificate_verify: YamlStr = SSL_CA_CERTIFICATE_VERIFY

    # When False, definitions declaring `network_forwarding` are rejected before Terraform apply.
    network_forwarding_enabled: YamlBool = False

    trc: TransformationConfiguration = Field(alias='sandbox_configuration')

    # Email allocation notifications
    smtp_server: YamlStr | None = None
    # Port of the used encryption protocol, ex. ssl, tsl
    smtp_port: YamlInt = 25
    smtp_encryption: Annotated[
        SMTPEncryption,
        _parsed_by(SMTPEncryption, _by_name(SMTPEncryption, 'smtp_encryption')),
        BY_NAME,
    ] = SMTPEncryption.INSECURE

    sender_email: YamlStr = 'sandbox.service@cyberrange.cz'
    sender_email_password: YamlStr | None = None

    netbird: NetbirdConfiguration | None = None

    trainee_sandbox_cleanup: TraineeSandboxCleanupConfiguration = Field(
        default_factory=TraineeSandboxCleanupConfiguration
    )

    lost_job_observer: LostJobObserverConfiguration = Field(
        default_factory=LostJobObserverConfiguration
    )

    @field_validator('git_rest_server')
    @classmethod
    def _valid_git_rest_server(cls, git_rest_server: str) -> str:
        crczp_config_validation.validate_git_rest_url(cls, git_rest_server)
        return git_rest_server

    @classmethod
    def _fix_node(cls, node: MappingNode) -> None:
        """Reject the legacy flat OpenStack keys, naming where each one moved."""
        legacy = [key_node for key_node, _ in node.value if key_node.value in LEGACY_OPENSTACK_KEYS]
        if legacy:
            moves = ', '.join(
                f'{key.value} -> openstack.{LEGACY_OPENSTACK_KEYS[key.value]}' for key in legacy
            )
            raise YamlModelError(f'OpenStack settings moved under `openstack:`: {moves}', legacy[0])

    def _after_load(self, node: MappingNode) -> None:
        """Make the ProxyJump IdentityFile absolute."""
        del node
        # ProxyJump IdentityFile must be an absolute path. abspath('') would give the cwd.
        if self.proxy_jump_to_man.IdentityFile:
            self.proxy_jump_to_man.IdentityFile = os.path.abspath(
                os.path.expanduser(self.proxy_jump_to_man.IdentityFile)
            )
