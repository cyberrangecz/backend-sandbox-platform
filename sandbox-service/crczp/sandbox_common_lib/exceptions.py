"""
Module containing exceptions.
All exceptions inherit from the ApiException class.
"""

from typing import ClassVar


class ApiException(Exception):
    """
    Base exception class for this project.
    All other exceptions inherit form it.
    """

    # HTTP status of the error response the exception handler turns the exception into.
    status_code: ClassVar[int] = 400


class DockerError(ApiException):
    """
    Raised when there is a problem with Docker container.
    """


class ValidationError(ApiException):
    """
    Raised when request contains invalid values.
    """


class ForbiddenError(ApiException):
    """
    Raised when the caller may not do what the request asks, e.g. with a wrong access token.
    """

    status_code = 403


class ConflictError(ApiException):
    """
    Raised when the request conflicts with the current state, e.g. a duplicate sandbox.
    """

    status_code = 409


class LimitExceededError(ApiException):
    """
    Raised when internally set limits are exceeded, eg. count of Ansible outputs.
    """


class NetworkError(ApiException):
    """
    Raised when call to external services fails.
    """


class GitError(ApiException):
    """
    For Git related errors.
    """


class StackError(ApiException):
    """
    Raised when application was not configured properly.
    """


class AnsibleError(ApiException):
    """
    Raised when there is some error during Ansible.
    """


class InterruptError(ApiException):
    """
    Raised when there is need to interrupt some action because of unspecified error.
    """


class ImproperlyConfigured(ApiException):
    """
    Raised when application was not configured properly.
    """


class EmailException(ApiException):
    """
    Raised when email notifications are not sent successfully.
    """
