"""
Import smoke test.

Importing `crczp.terraform_driver` pulls in both cloud drivers and structlog, so this
covers the whole dependency surface of the package. structlog in particular was imported
by terraform_exc_handlers.py while undeclared, arriving only via openstack-lib.
"""

from crczp.terraform_driver import (
    AvailableCloudLibraries,
    CrczpTerraformBackendType,
    CrczpTerraformClient,
    TerraformInstance,
)


def test_public_api_is_importable() -> None:
    for obj in (
        AvailableCloudLibraries,
        CrczpTerraformBackendType,
        CrczpTerraformClient,
        TerraformInstance,
    ):
        assert obj is not None


def test_both_cloud_libraries_are_selectable() -> None:
    """This enum is the only place either cloud driver is chosen."""
    assert {'OPENSTACK', 'AWS'} <= set(AvailableCloudLibraries.__members__)
