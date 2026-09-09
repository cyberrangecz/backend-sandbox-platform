"""Cloud utility functions for accessing OpenStack images."""

from typing import Any

from django.core.cache import cache

from crczp.sandbox_common_lib import utils

# Cached Image objects outlive the pod: bump the suffix when the Image fields change.
IMAGE_LIST_CACHE_KEY = 'image_list-v2'
IMAGE_LIST_CACHE_TIMEOUT = 60 * 60 * 24


def list_images(cached: bool = True) -> Any:
    """
    Get list of images as generator
    """
    if cached:
        image_set = cache.get(IMAGE_LIST_CACHE_KEY)
        if image_set is not None:
            return image_set

    client = utils.get_terraform_client()
    image_set = client.list_images()
    cache.set(IMAGE_LIST_CACHE_KEY, image_set, IMAGE_LIST_CACHE_TIMEOUT)
    return image_set
