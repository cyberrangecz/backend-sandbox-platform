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


class TerraformWorkspaceFailed(CrczpException):
    """
    This exception is raised if `terraform workspace` command fails.
    """


class TerraformStateLocked(CrczpException):
    """
    This exception is raised if a Terraform command fails on a held state lock.
    """

    def __init__(self, lock_id: str):
        super().__init__(f'Terraform state is locked by lock {lock_id}')
        self.lock_id = lock_id

    def __reduce__(self) -> tuple[type['TerraformStateLocked'], tuple[str]]:
        """Rebuild the exception from its lock id when it is pickled."""
        return type(self), (self.lock_id,)


class TerraformUnlockFailed(CrczpException):
    """
    This exception is raised if `terraform force-unlock` command fails.
    """
