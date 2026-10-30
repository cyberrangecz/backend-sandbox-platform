"""Tests for the status codes the custom exception handler answers with."""

import pytest
from rest_framework import status

from crczp.cloud_commons import CrczpException
from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_common_lib.exc_handler import custom_exception_handler


@pytest.mark.parametrize(
    ('exception', 'expected_status'),
    [
        (exceptions.GitError('Cannot find the repository'), status.HTTP_422_UNPROCESSABLE_ENTITY),
        (exceptions.ValidationError('Invalid definition'), status.HTTP_400_BAD_REQUEST),
        (exceptions.NetworkError('Call failed'), status.HTTP_400_BAD_REQUEST),
        (exceptions.StackError('Stack failed'), status.HTTP_400_BAD_REQUEST),
        (exceptions.ForbiddenError('Wrong token'), status.HTTP_403_FORBIDDEN),
        (exceptions.ConflictError('Already exists'), status.HTTP_409_CONFLICT),
        (CrczpException('OpenStack failed'), status.HTTP_400_BAD_REQUEST),
    ],
)
def test_api_exception_status(exception, expected_status):
    """Each project exception answers with its own status; the OpenStack lib's answer 400."""
    response = custom_exception_handler(exception, {})

    assert response.status_code == expected_status
    assert response.data == {'detail': str(exception)}
