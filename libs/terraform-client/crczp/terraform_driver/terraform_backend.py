"""
Module containing CyberRangeCZ Platform Terraform backend configuration.
"""

import os

from jinja2 import Environment, FileSystemLoader, select_autoescape

from crczp.terraform_driver.terraform_client_elements import CrczpTerraformBackendType
from crczp.terraform_driver.terraform_exceptions import TerraformImproperlyConfigured

TERRAFORM_STATE_FILE_NAME = 'terraform.tfstate'
TEMPLATES_DIR_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'templates')
TERRAFORM_BACKEND_FILE_NAME = 'terraform_backend.j2'


class CrczpTerraformBackend:  # pylint: disable=too-few-public-methods
    """Manages Terraform backend configuration and template rendering."""

    def __init__(
        self,
        backend_type: CrczpTerraformBackendType,
        db_configuration: dict[str, str] | None = None,
        kube_namespace: str | None = None,
    ):
        self.backend_type = backend_type
        self.db_configuration = db_configuration
        self.kube_namespace = kube_namespace
        self.template_environment = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR_PATH),
            autoescape=select_autoescape(),
        )
        self.template = self._create_terraform_backend_template()

    def _get_local_settings(self) -> str:
        return f'path = "{TERRAFORM_STATE_FILE_NAME}"'

    def _get_postgres_settings(self) -> str:
        if self.db_configuration is None:
            raise TerraformImproperlyConfigured(
                'Provide database configuration when using the postgres backend.'
            )

        conn_str = (
            f'postgres://{self.db_configuration["user"]}:{self.db_configuration["password"]}'
            f'@{self.db_configuration["host"]}/{self.db_configuration["name"]}?sslmode=disable'
        )
        return f'conn_str = "{conn_str}"'

    def _get_kubernetes_settings(self) -> str:
        if self.kube_namespace is None:
            raise TerraformImproperlyConfigured(
                'Provide Kubernetes namespace when using the kubernetes backend.'
            )

        return (
            f'secret_suffix = "state"\n'
            f'in_cluster_config = "true"\n'
            f'namespace = "{self.kube_namespace}"'
        )

    def _get_backend_settings(self) -> str:
        backend_settings = {
            CrczpTerraformBackendType.LOCAL: self._get_local_settings(),
            CrczpTerraformBackendType.POSTGRES: self._get_postgres_settings(),
            CrczpTerraformBackendType.KUBERNETES: self._get_kubernetes_settings(),
        }

        return backend_settings[self.backend_type]

    def _create_terraform_backend_template(self) -> str:
        """
        Create Terraform backend configuration
        :return: Terraform backend configuration
        """
        template = self.template_environment.get_template(TERRAFORM_BACKEND_FILE_NAME)
        return str(
            template.render(
                tf_backend=self.backend_type.value,
                tf_backend_settings=self._get_backend_settings(),
            )
        )
