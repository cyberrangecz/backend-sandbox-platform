"""Tests for crczp.openstack_driver.decorators module."""

import pytest
from keystoneauth1 import exceptions as keystone_exception
from openstack import exceptions as sdk_exceptions

from crczp.cloud_commons import CrczpException
from crczp.openstack_driver.decorators import check_authentication


class TestDecorators:
    """Tests for the check_authentication decorator."""

    @pytest.fixture()
    def dummy_function(self, mocker):
        """Create a MagicMock callable for decorator testing."""
        dummy_function = mocker.MagicMock()
        return dummy_function

    def test_check_authentication_not_authenticated(self, dummy_function):
        """Test that keystone ClientException is wrapped in CrczpException."""
        dummy_function.side_effect = keystone_exception.ClientException('testException')
        decorated_function = check_authentication(dummy_function)

        with pytest.raises(CrczpException):
            decorated_function()

    def test_check_authentication_decorated_function_raise_exception(self, dummy_function):
        """Test that non-keystone exceptions propagate unchanged."""
        dummy_function.side_effect = ValueError('testException')
        decorated_function = check_authentication(dummy_function)

        with pytest.raises(ValueError):
            decorated_function()

    def test_check_authentication(self, dummy_function):
        """Test that the decorated function is called with correct args."""
        decorated_function = check_authentication(dummy_function)

        decorated_function('args', kwargs='kwargs')
        dummy_function.assert_called_with('args', kwargs='kwargs')

    @pytest.mark.parametrize(
        'error',
        [
            # Proxy access when the service is missing from the catalog (openstacksdk 4.18).
            keystone_exception.EndpointNotFound('Could not find requested endpoint'),
            # Proxy access when the version discovery of the service failed.
            sdk_exceptions.NotSupported(
                'The image service for :RegionOne exists but does not have any supported versions.'
            ),
            # Proxy access when the service is missing from the catalog (openstacksdk 4.19+).
            sdk_exceptions.ServiceDisabledException('Service image is disabled'),
        ],
    )
    def test_check_authentication_service_unusable(self, dummy_function, error):
        """Test that a service that cannot be used at all is wrapped in CrczpException."""
        dummy_function.side_effect = error
        decorated_function = check_authentication(dummy_function)

        with pytest.raises(CrczpException) as exc_info:
            decorated_function()

        assert exc_info.value.__cause__ is error

    def test_check_authentication_http_error_passes_through(self, dummy_function):
        """Test that an HTTP error of an API call is not wrapped, so it stays a 500."""
        error = sdk_exceptions.NotFoundException('No Image found for 0000')
        dummy_function.side_effect = error
        decorated_function = check_authentication(dummy_function)

        with pytest.raises(sdk_exceptions.NotFoundException) as exc_info:
            decorated_function()

        assert exc_info.value is error
