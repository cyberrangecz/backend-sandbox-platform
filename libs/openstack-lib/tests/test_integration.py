import os

import pytest
import structlog
from Crypto.PublicKey import RSA
from crczp.cloud_commons import TransformationConfiguration

from crczp.openstack_driver import CrczpOpenStackClient

LOG = structlog.get_logger()


def generate_ssh_keypair(bits=2048):
    """
    Generate SSH-RSA key pair.

    :return: Tuple of private and public key strings
    """
    key = RSA.generate(bits)
    private_key = key.exportKey().decode()
    public_key = key.publickey().exportKey("OpenSSH").decode()
    return private_key, public_key


@pytest.mark.integration
class TestIntegration:
    key_pair_name = "lib-key-testing-1"
    sandbox_name = "lib-sb-testing-1"
    base_network_name = "test_base_network"
    base_network_stack_name = "test_base_net_stack"

    @pytest.fixture
    def os_client(self):
        auth_url = os.environ["OS_AUTH_URL"]
        application_credential_id = os.environ["OS_APPLICATION_CREDENTIAL_ID"]
        application_credential_secret = os.environ["OS_APPLICATION_CREDENTIAL_SECRET"]
        trc = TransformationConfiguration("debian-12-x86_64", "standard.small", "root", base_network=self.base_network_name)
        return CrczpOpenStackClient(auth_url, application_credential_id, application_credential_secret, trc)

    def test_up_and_down(self, os_client, topology_definition, base_network_template):
        self.create_keypair(os_client)
        self.delete_keypair(os_client)

    def create_keypair(self, os_client):
        _, public_key = generate_ssh_keypair()

        key_pairs = os_client.nova_client.keypairs.list()

        if self.key_pair_name in [key_pair.name for key_pair in key_pairs]:
            os_client.delete_keypair(self.key_pair_name)

        LOG.info("Issuing keypair CREATE command.", key_pair_name=self.key_pair_name)
        os_client.create_keypair(self.key_pair_name, public_key)

    def delete_keypair(self, os_client):
        LOG.info("Issuing keypair DELETE command.", key_pair_name=self.key_pair_name)
        os_client.delete_keypair(self.key_pair_name)
