import os

from jinja2 import Environment, FileSystemLoader
from kypo.cloud_commons import KypoException

from kypo.terraform_driver.terraform_client_elements import KypoTerraformBackendType

TERRAFORM_STATE_FILE_NAME = 'terraform.tfstate'
TEMPLATES_DIR_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'templates')
TERRAFORM_BACKEND_FILE_NAME = 'terraform_backend.j2'


class KypoTerraformBackend:

    def __init__(self, backend_type: KypoTerraformBackendType, db_configuration=None):
        self.backend_type = backend_type
        self.db_configuration = db_configuration
        self.template_environment = Environment(loader=(FileSystemLoader(TEMPLATES_DIR_PATH)))
        self.template = self._create_terraform_backend_template()

    def _get_state_file_location(self) -> str:
        """
        Get state file location for Terraform backend configuration.
        :return: State file locaiton
        """
        if self.backend_type == KypoTerraformBackendType.LOCAL:
            return TERRAFORM_STATE_FILE_NAME

        if self.db_configuration is None:
            raise KypoException(f'Cannot use backend "{self.backend_type.value()}" without'
                                f' specifying database configuration.')

        try:
            return 'postgres://{0[user]}:{0[password]}@{0[host]}/{0[name]}'\
                .format(self.db_configuration)
        except KeyError as exc:
            raise KypoException(f'Database configuration is incomplete. Error: "{exc}"')

    def _create_terraform_backend_template(self) -> str:
        """
        Create Terraform backend configuration
        :return: Terraform backend configuration
        """
        template = self.template_environment.get_template(TERRAFORM_BACKEND_FILE_NAME)
        return template.render(
            tf_backend=self.backend_type,
            tf_state_file_location=self._get_state_file_location(),
        )
