from typing import Any, Callable, TypeVar

from crczp.cloud_commons import exceptions
from keystoneauth1 import exceptions as keystone_exception

F = TypeVar('F', bound=Callable[..., Any])


def check_authentication(func: F) -> F:
    """Decorator for testing of OpenStackClient authentication and correct settings"""

    def call(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except keystone_exception.ClientException as ex:
            raise exceptions.CrczpException(
                f'Error while running CrczpOstackClient function: {ex}\n'
                'Either you are not authenticated or your configuration is wrong.'
            ) from ex

    return call  # type: ignore[return-value]
