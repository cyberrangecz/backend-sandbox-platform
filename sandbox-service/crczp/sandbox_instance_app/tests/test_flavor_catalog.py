"""Tests for the cloud flavor catalog and its view."""

from unittest import mock

import pytest
from django.conf import settings
from django.core.cache import cache
from rest_framework.reverse import reverse
from rest_framework.test import APIRequestFactory

from crczp.sandbox_cloud_app.lib.projects import get_flavor_catalog
from crczp.sandbox_cloud_app.views import ProjectFlavorsView

CLOUD_FLAVORS = {
    'b-flavor': {'vcpu': 2, 'ram': 4.0},
    'a-flavor': {'vcpu': 1, 'ram': 2.0},
}

FLAVOR_MAPPING = {
    'zeta.alias': 'a-flavor',
    'alpha.alias': 'a-flavor',
    'missing.second': 'gone-flavor',
    'missing.first': 'gone-flavor',
}


@pytest.fixture
def terraform_client(mocker):
    """Patch the terraform client with one offering the test cloud flavors."""
    client = mock.Mock()
    client.get_flavors_dict.return_value = CLOUD_FLAVORS
    mocker.patch('crczp.sandbox_common_lib.utils.get_terraform_client', return_value=client)
    return client


@pytest.fixture(autouse=True)
def empty_cache():
    """Run each test against an empty cache."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def crczp_config(mocker):
    """Replace the deployment configuration with one carrying the test flavor mapping."""
    return mocker.patch.object(settings, 'CRCZP_CONFIG', mock.Mock(flavor_mapping=FLAVOR_MAPPING))


def test_catalog_groups_aliases_and_sorts(terraform_client):  # pylint: disable=redefined-outer-name,unused-argument
    """Flavors, their aliases and the unmapped aliases come back sorted, RAM as ram_gb."""
    assert get_flavor_catalog() == {
        'flavors': [
            {
                'flavor': 'a-flavor',
                'aliases': ['alpha.alias', 'zeta.alias'],
                'resources': {'vcpu': 1, 'ram_gb': 2.0},
            },
            {'flavor': 'b-flavor', 'aliases': [], 'resources': {'vcpu': 2, 'ram_gb': 4.0}},
        ],
        'unmapped_aliases': ['missing.first', 'missing.second'],
    }


def test_catalog_without_flavor_mapping(terraform_client, crczp_config):  # pylint: disable=redefined-outer-name,unused-argument
    """A deployment with no flavor mapping lists every flavor without aliases."""
    crczp_config.flavor_mapping = None

    catalog = get_flavor_catalog()

    assert [flavor['aliases'] for flavor in catalog['flavors']] == [[], []]
    assert catalog['unmapped_aliases'] == []


def test_cached_catalog_reads_cloud_once(terraform_client):  # pylint: disable=redefined-outer-name
    """Repeated cached reads are served from the cache after the first cloud read."""
    get_flavor_catalog(cached=True)
    terraform_client.get_flavors_dict.return_value = {}

    catalog = get_flavor_catalog(cached=True)

    assert [flavor['flavor'] for flavor in catalog['flavors']] == ['a-flavor', 'b-flavor']
    terraform_client.get_flavors_dict.assert_called_once()


def test_uncached_catalog_refreshes_cache(terraform_client):  # pylint: disable=redefined-outer-name
    """An uncached read goes to the cloud and stores its result for later cached reads."""
    get_flavor_catalog(cached=True)
    terraform_client.get_flavors_dict.return_value = {'c-flavor': {'vcpu': 4, 'ram': 8.0}}

    uncached_catalog = get_flavor_catalog(cached=False)
    cached_catalog = get_flavor_catalog(cached=True)

    assert uncached_catalog == cached_catalog
    assert [flavor['flavor'] for flavor in cached_catalog['flavors']] == ['c-flavor']
    assert cached_catalog['unmapped_aliases'] == sorted(FLAVOR_MAPPING)


@pytest.mark.parametrize(
    ('query', 'expected_cached'),
    [('', True), ('?cached=true', True), ('?cached=false', False), ('?cached=FALSE', False)],
)
def test_view_passes_cached_query_parameter(mocker, query, expected_cached):
    """The view reads from the cache unless the cached query parameter is false."""
    get_catalog = mocker.patch(
        'crczp.sandbox_cloud_app.lib.projects.get_flavor_catalog',
        return_value={'flavors': [], 'unmapped_aliases': []},
    )
    request = APIRequestFactory().get(reverse('project-flavors') + query)

    response = ProjectFlavorsView.as_view()(request)

    assert response.status_code == 200
    assert response.data == {'flavors': [], 'unmapped_aliases': []}
    get_catalog.assert_called_once_with(cached=expected_cached)


@pytest.mark.parametrize(
    ('roles', 'expected_status'),
    [
        (['ROLE_SANDBOX-SERVICE_TRAINEE'], 403),
        (['ROLE_SANDBOX-SERVICE_DESIGNER'], 200),
        (['ROLE_SANDBOX-SERVICE_ORGANIZER'], 200),
        (['ROLE_SANDBOX-SERVICE_ADMIN'], 200),
    ],
)
def test_view_admits_designer_organizer_and_admin_only(mocker, roles, expected_status):
    """With REST authentication on, a trainee is refused and every other role is served."""
    mocker.patch.object(
        settings,
        'CRCZP_SERVICE_CONFIG',
        mock.Mock(authentication=mock.Mock(authenticated_rest_api=True)),
    )
    mocker.patch(
        'crczp.sandbox_uag.permissions.authenticator_class.get_bearer_token',
        return_value=b'token',
    )
    mocker.patch('crczp.sandbox_uag.permissions.get_user_roles', return_value=roles)
    mocker.patch(
        'crczp.sandbox_cloud_app.lib.projects.get_flavor_catalog',
        return_value={'flavors': [], 'unmapped_aliases': []},
    )
    request = APIRequestFactory().get(reverse('project-flavors'))

    response = ProjectFlavorsView.as_view()(request)

    assert response.status_code == expected_status
