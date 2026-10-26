"""Tests of the file name the SSH access downloads announce and of its CORS exposure."""

import io

import pytest
from corsheaders.middleware import CorsMiddleware
from django.http import HttpResponse, HttpResponseBase
from rest_framework.test import APIRequestFactory

from crczp.sandbox_instance_app.views import PoolManagementSSHAccessView, SandboxUserSSHAccessView

POOL_ID = 7
ALLOCATION_UNIT_ID = 11


@pytest.fixture
def request_factory() -> APIRequestFactory:
    return APIRequestFactory()


def test_management_ssh_access_names_the_zip_in_content_disposition(mocker, request_factory):
    mocker.patch('crczp.sandbox_instance_app.lib.pools.get_pool')
    mocker.patch(
        'crczp.sandbox_instance_app.lib.pools.get_management_ssh_access',
        return_value=io.BytesIO(b'zip'),
    )

    response = PoolManagementSSHAccessView().get(request_factory.get('/'), pool_id=POOL_ID)

    assert response['Content-Disposition'] == 'attachment; filename=ssh-access.zip'
    assert response['Content-Type'] == 'application/zip'


def test_user_ssh_access_names_the_zip_after_pool_and_allocation_unit(mocker, request_factory):
    sandbox = mocker.MagicMock()
    sandbox.allocation_unit.pool.id = POOL_ID
    sandbox.allocation_unit.id = ALLOCATION_UNIT_ID
    mocker.patch('crczp.sandbox_instance_app.lib.sandboxes.get_sandbox', return_value=sandbox)
    mocker.patch(
        'crczp.sandbox_instance_app.lib.sandboxes.get_user_ssh_access',
        return_value=io.BytesIO(b'zip'),
    )

    response = SandboxUserSSHAccessView().get(request_factory.get('/'), sandbox_uuid='uuid')

    assert response['Content-Disposition'] == (
        f'attachment; filename=user-ssh-access-pool-{POOL_ID}-sandbox-{ALLOCATION_UNIT_ID}.zip'
    )


def test_cors_exposes_content_disposition_to_browser_scripts(settings, request_factory):
    settings.CORS_ALLOW_ALL_ORIGINS = True
    cors_middleware = CorsMiddleware(lambda request: HttpResponse())

    response = cors_middleware(request_factory.get('/', HTTP_ORIGIN='https://frontend.example'))

    assert isinstance(response, HttpResponseBase)
    assert 'Content-Disposition' in response['Access-Control-Expose-Headers']
