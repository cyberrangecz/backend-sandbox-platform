"""Tests for CrczpTerraformClient pass-throughs to the cloud client."""

from unittest.mock import MagicMock

from crczp.terraform_driver import CrczpTerraformClient


def test_get_snapshot_sizes_delegates_to_cloud_client() -> None:
    """The snapshot lookup is answered by the cloud client."""
    client = CrczpTerraformClient.__new__(CrczpTerraformClient)
    client.cloud_client = MagicMock()
    client.cloud_client.get_snapshot_sizes.return_value = {'snap-0123abcd': 8}

    assert client.get_snapshot_sizes(['snap-0123abcd']) == {'snap-0123abcd': 8}
    client.cloud_client.get_snapshot_sizes.assert_called_once_with(['snap-0123abcd'])
