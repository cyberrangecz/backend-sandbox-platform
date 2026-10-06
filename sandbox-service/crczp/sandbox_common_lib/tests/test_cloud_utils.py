"""Tests for cloud client construction."""

import copy

import pytest
from django.conf import settings

from crczp.sandbox_common_lib import cloud_utils
from crczp.sandbox_common_lib.exceptions import ValidationError


def test_get_ostack_client_passes_openstack_credentials(mocker):
    """The client is built from the openstack section and the trc, nothing else cloud-specific."""
    client_class = mocker.patch.object(cloud_utils, 'CrczpTerraformClient')
    config = copy.copy(settings.CRCZP_CONFIG)
    config.openstack = copy.copy(config.openstack)
    config.openstack.auth_url = 'https://keystone.example/v3'
    config.openstack.application_credential_id = 'cred-id'
    config.openstack.application_credential_secret = 'cred-secret'  # nosec B105

    client = cloud_utils.get_ostack_client(config)

    assert client is client_class.return_value
    kwargs = client_class.call_args.kwargs
    assert set(kwargs) == {
        'auth_url',
        'application_credential_id',
        'application_credential_secret',
        'trc',
        'cloud_client',
        'backend_type',
        'db_configuration',
        'kube_namespace',
    }
    assert kwargs['auth_url'] == config.openstack.auth_url
    assert kwargs['application_credential_id'] == config.openstack.application_credential_id
    assert kwargs['application_credential_secret'] == config.openstack.application_credential_secret
    assert kwargs['trc'] is config.trc


def test_get_ostack_client_names_unset_option(mocker):
    """The error names the unset option and the alternative to configure."""
    mocker.patch.object(cloud_utils, 'CrczpTerraformClient')
    config = copy.copy(settings.CRCZP_CONFIG)
    config.openstack = copy.copy(config.openstack)
    config.openstack.auth_url = None

    with pytest.raises(
        ValidationError,
        match=r'options: openstack\.auth_url\. Configure either an `aws:` section',
    ):
        cloud_utils.get_ostack_client(config)
