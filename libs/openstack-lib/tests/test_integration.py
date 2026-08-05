"""Tests for CrczpOpenStackClient keypair lifecycle (mocked Nova API)."""

import pytest
from Crypto.PublicKey import RSA

from crczp.cloud_commons import TransformationConfiguration
from crczp.openstack_driver import CrczpOpenStackClient


def generate_ssh_keypair(bits=2048):
    """
    Generate SSH-RSA key pair.

    :return: Tuple of private and public key strings
    """
    key = RSA.generate(bits)
    private_key = key.exportKey().decode()
    public_key = key.publickey().exportKey('OpenSSH').decode()
    return private_key, public_key


class TestKeypairLifecycle:
    """Tests for keypair create/delete flow using a mocked Nova client."""

    key_pair_name = 'lib-key-testing-1'

    @pytest.fixture
    def os_client(self, mocker):
        """Create a CrczpOpenStackClient with a mocked Nova keypairs API."""
        trc = TransformationConfiguration(
            'debian-12-x86_64',
            'standard.small',
            'root',
            base_network='test_base_network',
        )
        client = CrczpOpenStackClient('http://fake-url', 'fake-id', 'fake-secret', trc)

        mock_keypairs = mocker.MagicMock()
        mock_keypairs.list.return_value = []
        mock_keypairs.create.return_value = mocker.MagicMock(name=self.key_pair_name)
        mock_keypairs.delete.return_value = None
        mock_keypairs.get.side_effect = Exception('not found')
        client.nova_client.keypairs = mock_keypairs
        client.open_stack_proxy.nova.keypairs = mock_keypairs

        return client

    def test_create_and_delete_keypair(self, os_client):
        """Test that a keypair can be created and deleted via the client."""
        _, public_key = generate_ssh_keypair()

        existing = os_client.nova_client.keypairs.list()
        assert self.key_pair_name not in [kp.name for kp in existing]

        os_client.create_keypair(self.key_pair_name, public_key)
        os_client.nova_client.keypairs.create.assert_called_once_with(
            self.key_pair_name, public_key, key_type='ssh'
        )

        os_client.delete_keypair(self.key_pair_name)
        os_client.nova_client.keypairs.delete.assert_called_once_with(self.key_pair_name)
