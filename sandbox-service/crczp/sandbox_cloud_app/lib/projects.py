"""Business logic for retrieving OpenStack project information."""

from typing import Any, TypedDict

from django.conf import settings
from django.core.cache import cache

from crczp.cloud_commons import Limits, QuotaSet
from crczp.sandbox_common_lib import utils

FLAVOR_DICT_CACHE_KEY = 'flavor_dict'
FLAVOR_DICT_CACHE_TIMEOUT = 60 * 5


class FlavorResources(TypedDict):
    """Compute resources one cloud flavor provides."""

    vcpu: int
    ram_gb: float


class Flavor(TypedDict):
    """One cloud flavor with the flavor mapping aliases translated to it."""

    flavor: str
    aliases: list[str]
    resources: FlavorResources


class FlavorCatalog(TypedDict):
    """Every cloud flavor, and the flavor mapping aliases translated to no cloud flavor."""

    flavors: list[Flavor]
    unmapped_aliases: list[str]


def get_quota_set() -> QuotaSet:
    """
    Get QuotaSet object.
    """
    client = utils.get_terraform_client()
    return client.get_quota_set()


def get_project_name() -> str:
    """
    Get current project name
    """
    client = utils.get_terraform_client()
    project_name: str = client.get_project_name()
    return project_name


def get_flavor_catalog(cached: bool = True) -> FlavorCatalog:
    """
    Get every cloud flavor with the flavor mapping aliases translated to it, sorted by name,
    and the aliases whose target flavor the cloud does not offer.
    """
    flavor_dict = _get_flavor_dict(cached)
    flavor_mapping = settings.CRCZP_CONFIG.flavor_mapping or {}
    aliases_by_flavor: dict[str, list[str]] = {}
    unmapped_aliases: list[str] = []
    for alias, target_flavor in flavor_mapping.items():
        if target_flavor in flavor_dict:
            aliases_by_flavor.setdefault(target_flavor, []).append(alias)
        else:
            unmapped_aliases.append(alias)
    return {
        'flavors': [
            {
                'flavor': name,
                'aliases': sorted(aliases_by_flavor.get(name, [])),
                'resources': {'vcpu': usage['vcpu'], 'ram_gb': usage['ram']},
            }
            for name, usage in sorted(flavor_dict.items())
        ],
        'unmapped_aliases': sorted(unmapped_aliases),
    }


def _get_flavor_dict(cached: bool) -> dict[str, Any]:
    """
    Get the cloud flavors keyed by name, from the cache when requested and present.
    """
    if cached:
        cached_flavors: dict[str, Any] | None = cache.get(FLAVOR_DICT_CACHE_KEY)
        if cached_flavors is not None:
            return cached_flavors

    client = utils.get_terraform_client()
    flavor_dict: dict[str, Any] = client.get_flavors_dict()
    cache.set(FLAVOR_DICT_CACHE_KEY, flavor_dict, FLAVOR_DICT_CACHE_TIMEOUT)
    return flavor_dict


def get_project_limits() -> Limits:
    """
    Get Absolute limits of OpenStack project.
    """
    client = utils.get_terraform_client()
    return client.get_project_limits()
