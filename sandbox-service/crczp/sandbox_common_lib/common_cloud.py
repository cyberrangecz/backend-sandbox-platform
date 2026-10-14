"""Cloud utility functions for accessing OpenStack images."""

from typing import Any

from django.core.cache import cache

from crczp.sandbox_common_lib import utils
from crczp.sandbox_common_lib.pagination import none_last_key

# Cached Image objects outlive the pod: bump the suffix when the Image fields or their order change.
IMAGE_LIST_CACHE_KEY = 'image_list-v3'
IMAGE_LIST_CACHE_TIMEOUT = 60 * 60 * 24


def list_images(cached: bool = True) -> Any:
    """
    Get the list of images ordered by name, then creation time; images without a value
    come last.
    """
    if cached:
        image_set = cache.get(IMAGE_LIST_CACHE_KEY)
        if image_set is not None:
            return image_set

    client = utils.get_terraform_client()
    image_set = sorted(
        client.list_images(),
        key=lambda image: (none_last_key(image.name), none_last_key(image.created_at)),
    )
    cache.set(IMAGE_LIST_CACHE_KEY, image_set, IMAGE_LIST_CACHE_TIMEOUT)
    return image_set
