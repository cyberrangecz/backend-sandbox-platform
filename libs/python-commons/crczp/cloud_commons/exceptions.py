"""
Module containing exceptions.
"""


class CrczpException(Exception):
    """
    Base exception class for this project. All other exceptions inherit form it.
    """
    pass


class StackException(CrczpException):
    """
    This exception is raised if error occurs within OpenStack API.
    """
    pass


class StackCreationFailed(StackException):
    """
    This exception is raised if error occurs while creating stack.
    """
    pass


class StackNotFound(StackException):
    """
    This exception is raised if Terraform stack directory is not found.
    """
    pass


class InvalidTopologyDefinition(CrczpException):
    """
    This exception is raised if topology definition cannot be transformed.
    """
    def __init__(self, message: str, *args):
        self.message = "Topology definition could not be transformed: " + message
        super(InvalidTopologyDefinition, self).__init__(self.message, *args)
