"""Tests for crczp.openstack_driver.utils module."""

import keystoneauth1.session
from openstack import connection

from crczp.openstack_driver import utils


class TestUtils:
    """Tests for utility functions (get_connection, get_session)."""

    def test_get_connection(self, mocker):
        """Test that get_connection wraps the session, pinned like the legacy clients."""
        mocker.patch.object(
            keystoneauth1.session.Session, 'request', side_effect=AssertionError('network I/O')
        )
        session = utils.get_session('http://keystone.test:5000/v3', 'testId', 'testSecret')

        conn = utils.get_connection(session)

        assert isinstance(conn, connection.Connection)
        assert conn.session is session
        assert conn.config.get_api_version('compute') == '2.10'
        assert conn.config.get_api_version('image') == '2'
        assert conn.config.get_status_code_retries('image') == 0

    def test_get_session(self):
        """Test that get_session returns a valid Session."""
        session = utils.get_session('testUrl', 'testId', 'testSecret')
        assert isinstance(session, keystoneauth1.session.Session)
