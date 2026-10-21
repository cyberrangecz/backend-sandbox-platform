"""Tests for CrczpOpenStackClient keypair lifecycle (fake openstacksdk connection)."""

import pytest
from Crypto.PublicKey import RSA  # noqa: S413  # pycryptodome, not the abandoned pycrypto
from openstack.compute.v2.keypair import Keypair

from crczp.cloud_commons import TransformationConfiguration
from crczp.openstack_driver import CrczpOpenStackClient, utils


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
    """Tests for keypair create/delete flow through the client, proxy and fake SDK."""

    key_pair_name = 'lib-key-testing-1'

    @pytest.fixture
    def os_client(self, mocker, fake_conn):
        """Create a CrczpOpenStackClient whose openstacksdk connection is fake_conn."""
        trc = TransformationConfiguration(
            'debian-12-x86_64',
            'standard.small',
            'root',
            base_network='test_base_network',
        )
        mocker.patch.object(utils, 'get_connection', return_value=fake_conn)
        return CrczpOpenStackClient('http://fake-url', 'fake-id', 'fake-secret', trc)

    def test_create_and_delete_keypair(self, os_client, fake_conn):
        """Test that a keypair can be created and deleted via the client."""
        _, public_key = generate_ssh_keypair()
        fake_conn.compute.create_keypair.return_value = Keypair.existing(
            name=self.key_pair_name, public_key=public_key, type='ssh'
        )

        os_client.create_keypair(self.key_pair_name, public_key)
        fake_conn.compute.create_keypair.assert_called_once_with(
            name=self.key_pair_name, public_key=public_key, type='ssh'
        )

        os_client.delete_keypair(self.key_pair_name)
        fake_conn.compute.delete_keypair.assert_called_once_with(
            self.key_pair_name, ignore_missing=False
        )
