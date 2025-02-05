import pytest
from keystoneauth1 import exceptions as keystone_exception
from crczp.cloud_commons import CrczpException

from crczp.openstack_driver.decorators import check_authentication


class TestDecorators:
    @pytest.fixture()
    def dummy_function(self, mocker):
        dummy_function = mocker.MagicMock()
        return dummy_function

    def test_check_authentication_not_authenticated(self, dummy_function):
        dummy_function.side_effect = keystone_exception.ClientException("testException")
        decorated_function = check_authentication(dummy_function)

        with pytest.raises(CrczpException):
            decorated_function()

    def test_check_authentication_decorated_function_raise_exception(self, dummy_function):
        dummy_function.side_effect = ValueError("testException")
        decorated_function = check_authentication(dummy_function)

        with pytest.raises(ValueError):
            decorated_function()

    def test_check_authentication(self, dummy_function):
        decorated_function = check_authentication(dummy_function)

        decorated_function("args", kwargs="kwargs")
        dummy_function.assert_called_with("args", kwargs="kwargs")
