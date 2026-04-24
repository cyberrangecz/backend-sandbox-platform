"""
Module containing CyberRangeCZ Platform Terraform exceptions.
"""

from crczp.cloud_commons import CrczpException


class TerraformImproperlyConfigured(CrczpException):  # type: ignore[misc]
    """
    This exception is raised if the incorrect configuration is provided
    """


class TerraformInitFailed(CrczpException):  # type: ignore[misc]
    """
    This exception is raised if 'terraform init' command fails.
    """


class TerraformWorkspaceFailed(CrczpException):  # type: ignore[misc]
    """
    This exception is raised if `terraform workspace` command fails.
    """
