"""Decorators for OpenStack client authentication checks."""

from typing import Callable, ParamSpec, TypeVar

from keystoneauth1 import exceptions as keystone_exception

from crczp.cloud_commons import exceptions

P = ParamSpec('P')
T = TypeVar('T')


def check_authentication(func: Callable[P, T]) -> Callable[P, T]:
    """Decorator for testing of OpenStackClient authentication and correct settings"""

    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return func(*args, **kwargs)
        except keystone_exception.ClientException as ex:
            raise exceptions.CrczpException(
                f'Error while running CrczpOstackClient function: {ex}\n'
                'Either you are not authenticated or your configuration is wrong.'
            ) from ex

    return call
