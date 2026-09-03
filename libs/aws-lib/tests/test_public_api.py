"""
Import smoke test: fails if any runtime dependency of this package is undeclared.
"""

from crczp.cloud_commons import CrczpCloudClientBase

from crczp.aws_driver.aws_client import CrczpAwsClient


def test_client_is_importable() -> None:
    assert CrczpAwsClient is not None


def test_client_implements_the_cloud_contract() -> None:
    """This driver is only useful as an implementation of the shared ABC."""
    assert issubclass(CrczpAwsClient, CrczpCloudClientBase)
    assert not CrczpAwsClient.__abstractmethods__, 'unimplemented abstract methods remain'
