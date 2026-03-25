"""
Module containing exceptions.
"""

from typing import Any


class CrczpException(Exception):
    """
    Base exception class for this project. All other exceptions inherit form it.
    """


class StackException(CrczpException):
    """
    This exception is raised if error occurs within OpenStack API.
    """


class StackCreationFailed(StackException):
    """
    This exception is raised if error occurs while creating stack.
    """


class StackNotFound(StackException):
    """
    This exception is raised if Terraform stack directory is not found.
    """


class InvalidTopologyDefinition(CrczpException):
    """
    This exception is raised if topology definition cannot be transformed.
    """

    def __init__(self, message: str, *args: Any) -> None:
        self.message = 'Topology definition could not be transformed: ' + message
        super().__init__(self.message, *args)
