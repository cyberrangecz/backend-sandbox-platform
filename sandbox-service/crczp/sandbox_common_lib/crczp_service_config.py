"""
REST API configuration file
"""

from typing import IO, Any, Self

from pydantic import Field, field_validator

from crczp.sandbox_common_lib.crczp_config import CrczpConfiguration
from crczp.sandbox_common_lib.exceptions import ImproperlyConfigured
from crczp.topology_definition.models import StrList
from crczp.topology_definition.yaml_model import YamlBool, YamlModel, YamlModelError, YamlStr

STACK_NAME_PREFIX = 'default0'
MICROSERVICE_NAME = 'sandbox-service'
DEBUG = True
# Insecure development default; deployments must set django_secret_key in config.yml.
DJANGO_SECRET_KEY = '-^mu0=6s@*x4jdbrz5yr!++p*02#%m$_4&0uw8h1)&r5u!v=12'  # noqa: S105
ALLOWED_HOSTS = ['*']  # Allow everyone
CORS_ORIGIN_ALLOW_ALL = True
CORS_ORIGIN_WHITELIST: list[str] = []
AUTHENTICATED_REST_API = False
ALLOWED_OIDC_PROVIDERS: list[dict[str, str]] = []


def stack_name_prefix_validator(_: object, value: str) -> None:
    """Validate that stack_name_prefix is between 1 and 8 characters long."""
    if len(value) < 1 or len(value) > 8:
        raise ValueError('Value stack_name_prefix can have only 1 to 8 characters')


AllowedOidcProviders = list[dict[str, Any]]


class Authentication(YamlModel):
    """Authentication configuration for the sandbox service."""

    authenticated_rest_api: YamlBool = AUTHENTICATED_REST_API
    allowed_oidc_providers: AllowedOidcProviders = Field(
        default_factory=lambda: list(ALLOWED_OIDC_PROVIDERS)
    )
    roles_registration_url: YamlStr
    roles_acquisition_url: YamlStr


class CrczpServiceConfig(YamlModel):
    """Top-level service configuration combining Django and app settings."""

    stack_name_prefix: YamlStr = STACK_NAME_PREFIX
    microservice_name: YamlStr = MICROSERVICE_NAME
    debug: YamlBool = DEBUG
    django_secret_key: YamlStr = DJANGO_SECRET_KEY
    allowed_hosts: StrList = Field(default_factory=lambda: list(ALLOWED_HOSTS))
    cors_origin_allow_all: YamlBool = CORS_ORIGIN_ALLOW_ALL
    cors_origin_whitelist: StrList = Field(default_factory=lambda: list(CORS_ORIGIN_WHITELIST))

    authentication: Authentication
    app_config: CrczpConfiguration = Field(alias='application_configuration')

    @field_validator('stack_name_prefix')
    @classmethod
    def _valid_stack_name_prefix(cls, value: str) -> str:
        stack_name_prefix_validator(cls, value)
        return value

    @classmethod
    def load(cls, stream: str | IO[str]) -> Self:
        """Load the service configuration, reporting an invalid one as ImproperlyConfigured."""
        try:
            return super().load(stream)
        except YamlModelError as ex:
            raise ImproperlyConfigured(ex) from ex
