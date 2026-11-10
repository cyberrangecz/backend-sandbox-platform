"""Decorators for OpenStack client authentication checks."""

from collections.abc import Callable

from keystoneauth1 import exceptions as keystone_exception
from openstack import exceptions as sdk_exceptions

from crczp.cloud_commons import exceptions

# Raised when a service cannot be reached or used at all: authentication and connection
# failures (keystoneauth), a service whose version discovery failed on first use
# (NotSupported), and a service missing from the catalog on openstacksdk 4.19+
# (ServiceDisabledException). HTTP errors of an individual API call are not among them.
_AUTH_OR_CONFIG_ERRORS = (
    keystone_exception.ClientException,
    sdk_exceptions.NotSupported,
    sdk_exceptions.ServiceDisabledException,
)


def check_authentication[**P, T](func: Callable[P, T]) -> Callable[P, T]:
    """Decorator for testing of OpenStackClient authentication and correct settings"""

    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return func(*args, **kwargs)
        except _AUTH_OR_CONFIG_ERRORS as ex:
            raise exceptions.CrczpException(
                f'Error while running CrczpOstackClient function: {ex}\n'
                'Either you are not authenticated or your configuration is wrong.'
            ) from ex

    return call
