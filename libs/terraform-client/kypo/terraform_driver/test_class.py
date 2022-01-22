import os

from kypo.terraform_driver.terraform_client import KypoTerraformClient, AvailableCloudLibraries
from kypo.topology_definition.models import TopologyDefinition


class TestClass(KypoTerraformClient):

    def create_terraform_template(self, topology_definition: TopologyDefinition, *args, **kwargs):
        print(os.path.dirname(os.path.realpath(__file__)))
    def test(self):
        pass


TestClass(AvailableCloudLibraries.OPENSTACK, auth_url='https://identity.cloud.muni.cz/v3',
          application_credential_id='beb3b72c96284c60b1f45cec0974df25',
          application_credential_secret='opcG5WaQrqxUfviXVfs8Poezd1a8O0MIdLpcrhy76eC29cPl7Tn0XvvauRazgXH08GNu2uSUA5lCWZe3BUX5MA',
          trc='trc')
