"""Unit tests for the custom DRF exception handler."""

import pytest

from crczp.cloud_commons import CrczpException
from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_common_lib.exc_handler import custom_exception_handler


@pytest.mark.parametrize(
    ('exc', 'expected_status'),
    [
        (exceptions.ValidationError('invalid count'), 400),
        (exceptions.StackError('stack failed'), 400),
        (exceptions.ForbiddenError('wrong token'), 403),
        (exceptions.ConflictError('already exists'), 409),
        (CrczpException('openstack failed'), 400),
    ],
)
def test_api_exceptions_answer_with_their_status(exc, expected_status):
    """Each project exception carries its status; the OpenStack lib's answer 400."""
    response = custom_exception_handler(exc, {})

    assert response.status_code == expected_status
    assert response.data == {'detail': str(exc)}
