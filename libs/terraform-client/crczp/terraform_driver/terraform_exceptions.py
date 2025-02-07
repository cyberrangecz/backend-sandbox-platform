"""
Module containing CyberRangeCZ Platform Terraform exceptions.
"""

from crczp.cloud_commons import CrczpException


class TerraformImproperlyConfigured(CrczpException):
    """
    This exception is raised if the incorrect configuration is provided
    """


class TerraformInitFailed(CrczpException):
    """
    This exception is raised if 'terraform init' command fails.
    """
    pass


class TerraformWorkspaceFailed(CrczpException):
    """
    This exception is raised if `terraform workspace` command fails.
    """
    pass
