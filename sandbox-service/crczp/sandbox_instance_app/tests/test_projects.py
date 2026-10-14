"""Tests for image listing and caching via common_cloud."""

from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.cache import cache

from crczp.sandbox_common_lib.common_cloud import list_images

IMAGES = [SimpleNamespace(name=f'image{index}', created_at=None) for index in range(1, 4)]
OTHER_IMAGES = [SimpleNamespace(name=f'image{index}', created_at=None) for index in range(4, 6)]


@pytest.fixture
def mock_terraform_client():
    """Create a mock Terraform client that returns a fixed image list."""
    client = mock.Mock()
    client.list_images.return_value = IMAGES
    return client


@pytest.fixture
def setup_cache():
    """Clear the cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_list_images_cached(  # pylint: disable=unused-argument,redefined-outer-name
    mock_terraform_client, setup_cache
):
    """Test that list_images returns cached results on second call when cached=True."""
    with mock.patch(
        'crczp.sandbox_common_lib.utils.get_terraform_client', return_value=mock_terraform_client
    ):
        images = list_images(cached=True)
        assert images == IMAGES

        mock_terraform_client.list_images.return_value = OTHER_IMAGES

        images = list_images(cached=True)
        assert images == IMAGES


@pytest.mark.django_db
def test_list_images_not_cached(  # pylint: disable=unused-argument,redefined-outer-name
    mock_terraform_client, setup_cache
):
    """Test that list_images fetches fresh results on each call when cached=False."""
    with mock.patch(
        'crczp.sandbox_common_lib.utils.get_terraform_client', return_value=mock_terraform_client
    ):
        images = list_images(cached=False)
        assert images == IMAGES

        mock_terraform_client.list_images.return_value = OTHER_IMAGES

        images = list_images(cached=False)
        assert images == OTHER_IMAGES
