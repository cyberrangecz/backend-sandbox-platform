import os

from jinja2 import Environment, FileSystemLoader

from crczp.terraform_driver.terraform_client_elements import CrczpTerraformBackendType
from crczp.terraform_driver.terraform_exceptions import TerraformImproperlyConfigured

TERRAFORM_STATE_FILE_NAME = 'terraform.tfstate'
TEMPLATES_DIR_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'templates')
TERRAFORM_BACKEND_FILE_NAME = 'terraform_backend.j2'


class CrczpTerraformBackend:

    def __init__(self, backend_type: CrczpTerraformBackendType, db_configuration=None, kube_namespace=None):
        self.backend_type = backend_type
        self.db_configuration = db_configuration
        self.kube_namespace = kube_namespace
        self.template_environment = Environment(loader=(FileSystemLoader(TEMPLATES_DIR_PATH)))
        self.template = self._create_terraform_backend_template()

    def _get_local_settings(self) -> str:
        return f'path = "{TERRAFORM_STATE_FILE_NAME}"'

    def _get_postgres_settings(self) -> str:
        if self.db_configuration is None:
            raise TerraformImproperlyConfigured('Provide database configuration when using the postgres backend.')

        conn_str = 'postgres://{0[user]}:{0[password]}@{0[host]}/{0[name]}?sslmode=disable'\
                   .format(self.db_configuration)
        return f'conn_str = "{conn_str}"'

    def _get_kubernetes_settings(self) -> str:
        if self.kube_namespace is None:
            raise TerraformImproperlyConfigured('Provide Kubernetes namespace when using the kubernetes backend.')

        return f'secret_suffix = "state"\nin_cluster_config = "true"\nnamespace = "{self.kube_namespace}"'

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
        return template.render(
            tf_backend=self.backend_type.value,
            tf_backend_settings=self._get_backend_settings(),
        )
