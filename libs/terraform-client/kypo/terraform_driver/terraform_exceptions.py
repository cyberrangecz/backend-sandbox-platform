"""
Module containing KYPO Terraform exceptions.
"""

from kypo.cloud_commons import KypoException


class TerraformInitFailed(KypoException):
    """
    This exception is raised if 'terraform init' command fails.
    """
    pass


class TerraformWorkspaceFailed(KypoException):
    """
    This exception is raised if `terraform workspace` command fails.
    """
    pass
