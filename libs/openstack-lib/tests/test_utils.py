"""Tests for crczp.openstack_driver.utils module."""

from contextlib import contextmanager

import keystoneauth1.session
import pytest
from novaclient.v2 import client as nova_client

from crczp.openstack_driver import utils


class TestUtils:
    """Tests for utility functions (get_client, get_session)."""

    @staticmethod
    def raises(error):
        """Wrapper around pytest.raises to support None."""
        if error:
            return pytest.raises(error)

        @contextmanager
        def not_raises():
            try:
                yield
            except Exception as e:
                raise e

        return not_raises()

    @pytest.mark.parametrize(
        'client_type, expected_client, exception',
        [
            ('neutron', utils.neutron_client.Client, None),
            ('glance', utils.glance_client.Client, None),
            ('nova', nova_client.Client, None),
            ('badClient', nova_client.Client, ValueError),
            ('neutron', nova_client.Client, AssertionError),
        ],
    )
    def test_get_client(self, client_type, expected_client, exception):
        """Test that get_client returns the correct client type."""
        with self.raises(exception):
            client = utils.get_client(client_type, keystoneauth1.session.Session())
            assert isinstance(client, expected_client)

    def test_get_session(self):
        """Test that get_session returns a valid Session."""
        session = utils.get_session('testUrl', 'testId', 'testSecret')
        assert isinstance(session, keystoneauth1.session.Session)
