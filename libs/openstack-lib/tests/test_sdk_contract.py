"""Wire-level contract of the OpenStack API calls the driver makes through openstacksdk.

The client runs with its real keystoneauth session and openstacksdk connection
(utils.get_session, utils.get_connection). Only the HTTP transport is replaced: a fake cloud
mounted on the session answers keystone, nova, glance and neutron with realistic bodies, and
records every request. The tests pin method, path, query, JSON body and the compute
microversion header of each call, and the parsing of the answers.

The autospecced fakes of the other test modules cannot do this: the SDK methods used here
accept any keyword argument, and the parsing happens in the SDK resources. So these tests are
what shows a behaviour change in a future openstacksdk.
"""

# pytest passes the cloud and client fixtures of this module to the tests by name.
# pylint: disable=redefined-outer-name
# Every call is pinned here, against the one fake cloud and fixtures above the tests.
# pylint: disable=too-many-lines

import json
from collections.abc import Callable
from typing import Any, NamedTuple
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from openstack import exceptions as sdk_exceptions
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from crczp.cloud_commons import (
    CrczpException,
    Limits,
    Quota,
    QuotaSet,
    StackException,
    StackNameScheme,
)
from crczp.openstack_driver import CrczpOpenStackClient, utils

AUTH_URL = 'http://keystone.test:5000/v3'
NOVA_URL = 'http://nova.test:8774/v2.1'
GLANCE_URL = 'http://glance.test:9292'
NEUTRON_URL = 'http://neutron.test:9696'

PROJECT_ID = '5ef70662f8b34079a6eddb8da9d75fe8'
USER_ID = '8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a'
SERVER_ID = '9a3e6c1b-2f4d-4b8a-8e7c-5d1f0a2b3c4d'
KEYPAIR_NAME = 'crczp-p0000000012-3f2c9a4e-8b1d-4c6e-9a7f-2d5b8e1c0a93-ssh'
PUBLIC_KEY = (
    'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK0wmN/Cr3JXqmLW7u+g9pTh+wy3Kx2LjMjQ3fM0dWg5 crczp'
)
FINGERPRINT = 'SHA256:0bN3v1aJqY6h2c5n0yQ5o0m3o0Zb2l4q8yM1sQ7tF6E'
REQUEST_ID = 'req-4f1c2a3b-5d6e-4f70-8a9b-0c1d2e3f4a5b'
COMPUTE_2_10 = 'compute 2.10'

NOVA_VERSION = {
    'id': 'v2.1',
    'status': 'CURRENT',
    'version': '2.96',
    'min_version': '2.1',
    'updated': '2013-07-23T11:33:21Z',
    'links': [{'rel': 'self', 'href': f'{NOVA_URL}/'}],
    'media-types': [
        {'base': 'application/json', 'type': 'application/vnd.openstack.compute+json;version=2.1'}
    ],
}
GLANCE_VERSIONS = {
    'versions': [
        {
            'id': f'v2.{minor}',
            'status': 'CURRENT' if minor == 16 else 'SUPPORTED',
            'links': [{'rel': 'self', 'href': f'{GLANCE_URL}/v2/'}],
        }
        for minor in range(16, 0, -1)
    ]
}


class Call(NamedTuple):
    """An API request as the fake cloud saw it."""

    method: str
    url: str
    query: dict[str, list[str]]
    body: Any
    microversion: str | None


class Reply(NamedTuple):
    """The answer of the fake cloud to one request."""

    status: int
    body: Any = None
    # A None value drops one of the default headers.
    headers: dict[str, str | None] = {}


def _nova_fault(status: int, fault: str, message: str) -> Reply:
    return Reply(status, {fault: {'code': status, 'message': message}})


class FakeCloud(BaseAdapter):
    """A requests transport that answers from a route table and records every request.

    Routes are keyed by method and URL including the query string. A request the driver is
    not expected to send fails the test, so it can never pass as an expected error answer.
    """

    def __init__(self) -> None:
        super().__init__()
        self.routes: dict[tuple[str, str], Reply | Callable[[Any], Reply]] = {}
        self.calls: list[Call] = []
        self._discovery: set[tuple[str, str]] = set()

    def route(
        self, method: str, url: str, reply: Reply | Callable[[Any], Reply], discovery: bool = False
    ) -> None:
        """Answer `method url` with `reply`, or with what `reply(json_body)` returns."""
        self.routes[method, url] = reply
        if discovery:
            self._discovery.add((method, url))

    def api_calls(self) -> list[Call]:
        """The recorded requests, without the token request and version discovery."""
        return [
            call
            for call in self.calls
            if not call.url.startswith(AUTH_URL) and (call.method, call.url) not in self._discovery
        ]

    def send(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None
    ) -> requests.Response:
        body = request.body.decode() if isinstance(request.body, bytes) else request.body
        json_body = json.loads(body) if body else None
        url = request.url.split('?', 1)[0]
        self.calls.append(
            Call(
                request.method,
                url,
                parse_qs(urlsplit(request.url).query),
                json_body,
                request.headers.get('OpenStack-API-Version'),
            )
        )
        if (request.method, url) in self._discovery:
            route = self.routes[request.method, url]
        else:
            route = self.routes.get((request.method, request.url))
            if route is None:
                raise AssertionError(f'unexpected request: {request.method} {request.url}')
        reply = route if isinstance(route, Reply) else route(json_body)

        response = requests.Response()
        response.status_code = reply.status
        response.reason = 'Fake'
        headers = {
            'Content-Type': 'application/json',
            'x-openstack-request-id': REQUEST_ID,
            **reply.headers,
        }
        response.headers = CaseInsensitiveDict({
            name: value for name, value in headers.items() if value is not None
        })
        if reply.body is None:
            response._content = b''  # pylint: disable=protected-access
        elif isinstance(reply.body, str):
            response._content = reply.body.encode()  # pylint: disable=protected-access
        else:
            response._content = json.dumps(reply.body).encode()  # pylint: disable=protected-access
        response.encoding = 'utf-8'
        response.url = request.url
        response.request = request
        return response

    def close(self) -> None:
        pass


def _catalog_entry(service_type: str, name: str, url: str) -> dict[str, Any]:
    internal_url = url.replace('.test', '-internal.test')
    return {
        'id': f'{name}-service-id',
        'type': service_type,
        'name': name,
        'endpoints': [
            {
                'id': f'{name}-{interface}-id',
                'interface': interface,
                'region': 'RegionOne',
                'region_id': 'RegionOne',
                'url': url if interface == 'public' else internal_url,
            }
            for interface in ('public', 'internal', 'admin')
        ],
    }


# What keystone answers to the application credential's token request.
TOKEN = {
    'token': {
        'methods': ['application_credential'],
        'user': {
            'id': USER_ID,
            'name': 'crczp-service',
            'domain': {'id': 'default', 'name': 'Default'},
            'password_expires_at': None,
        },
        'audit_ids': ['kL3m9QqfS1a2b3c4d5e6fw'],
        'expires_at': '2099-01-01T00:00:00.000000Z',
        'issued_at': '2026-10-19T08:00:00.000000Z',
        'project': {
            'id': PROJECT_ID,
            'name': 'crczp-devel',
            'domain': {'id': 'default', 'name': 'Default'},
        },
        'is_domain': False,
        'roles': [{'id': '9fe2ff9ee4384b1894a90878d3e92bab', 'name': 'member'}],
        'application_credential': {
            'id': 'app-cred-id',
            'name': 'crczp-sandbox-service',
            'restricted': True,
        },
        'catalog': [
            _catalog_entry('compute', 'nova', NOVA_URL),
            _catalog_entry('image', 'glance', GLANCE_URL),
            _catalog_entry('network', 'neutron', NEUTRON_URL),
        ],
    }
}


@pytest.fixture
def cloud():
    """Create the fake cloud, with a working keystone and version discovery."""
    fake = FakeCloud()
    fake.route('POST', f'{AUTH_URL}/auth/tokens', Reply(201, TOKEN, {'X-Subject-Token': 'gAAAAAB'}))
    fake.route('GET', NOVA_URL, Reply(200, {'version': NOVA_VERSION}), discovery=True)
    fake.route('GET', f'{GLANCE_URL}/', Reply(300, GLANCE_VERSIONS), discovery=True)
    return fake


@pytest.fixture
def client(mocker, trc, cloud):
    """Create the client with its real session and connection, talking to the fake cloud."""
    get_session = utils.get_session

    def get_session_on_fake_cloud(*args: str) -> Any:
        session = get_session(*args)
        session.session.mount('http://', cloud)
        return session

    mocker.patch.object(utils, 'get_session', side_effect=get_session_on_fake_cloud)
    return CrczpOpenStackClient(AUTH_URL, 'app-cred-id', 'app-cred-secret', trc)


def _nova(method: str, path: str, body: Any = None, **query: list[str]) -> Call:
    """An expected compute request: every one carries the pinned microversion."""
    return Call(method, f'{NOVA_URL}{path}', query, body, COMPUTE_2_10)


class TestImages:
    """glance: GET /v2/images and /v2/images/{id}."""

    def test_list_images_follows_next_links(
        self, client, cloud, glance_image, glance_image_without_properties, glance_image_fields
    ):
        """Two pages: no page size is sent, and only glance's `next` link is followed."""
        kali_id = glance_image['id']
        cloud.route(
            'GET',
            f'{GLANCE_URL}/v2/images',
            Reply(
                200,
                {
                    'images': [glance_image],
                    'first': '/v2/images',
                    'schema': '/v2/schemas/images',
                    'next': f'/v2/images?marker={kali_id}',
                },
            ),
        )
        cloud.route(
            'GET',
            f'{GLANCE_URL}/v2/images?marker={kali_id}',
            Reply(
                200,
                {
                    'images': [glance_image_without_properties],
                    'first': '/v2/images',
                    'schema': '/v2/schemas/images',
                },
            ),
        )

        images = client.list_images()

        assert cloud.api_calls() == [
            Call('GET', f'{GLANCE_URL}/v2/images', {}, None, None),
            Call('GET', f'{GLANCE_URL}/v2/images', {'marker': [kali_id]}, None, None),
        ]
        assert [vars(image) for image in images] == [
            glance_image_fields,
            {
                'os_distro': None,
                'os_type': None,
                'disk_format': 'raw',
                'container_format': 'bare',
                'visibility': 'public',
                'size': 629145600,
                'status': 'active',
                'min_ram': 0,
                'min_disk': 0,
                'created_at': '2026-02-03T14:05:51Z',
                'updated_at': '2026-02-03T14:06:10Z',
                'tags': [],
                'default_user': None,
                'name': 'ubuntu-noble-man-0.1.0',
                'owner_specified': {},
            },
        ]

    def test_get_image(self, client, cloud, glance_image):
        """The image is fetched by its ID and its custom properties are read."""
        cloud.route('GET', f'{GLANCE_URL}/v2/images/{glance_image["id"]}', Reply(200, glance_image))

        image = client.get_image(glance_image['id'])

        assert cloud.api_calls() == [
            Call('GET', f'{GLANCE_URL}/v2/images/{glance_image["id"]}', {}, None, None)
        ]
        assert image.name == 'kali-0.6.0'
        assert image.default_user == 'kali'
        assert image.owner_specified['owner_specified.openstack.custom'] == 'true'

    def test_get_image_not_found(self, client, cloud):
        """A missing image is not wrapped, so the API keeps answering 500."""
        image_id = '00000000-0000-0000-0000-000000000000'
        cloud.route(
            'GET',
            f'{GLANCE_URL}/v2/images/{image_id}',
            Reply(
                404,
                '<html><head><title>404 Not Found</title></head><body><h1>404 Not Found</h1>'
                f'No image found with ID {image_id}<br /><br /></body></html>',
                {'Content-Type': 'text/html; charset=UTF-8'},
            ),
        )

        with pytest.raises(sdk_exceptions.NotFoundException):
            client.get_image(image_id)

        assert cloud.api_calls() == [
            Call('GET', f'{GLANCE_URL}/v2/images/{image_id}', {}, None, None)
        ]

    def test_image_discovery_failure_is_crczp_exception(self, client, cloud):
        """A glance that fails its version discovery is reported like an unreachable service."""
        cloud.route(
            'GET',
            f'{GLANCE_URL}/',
            Reply(
                503,
                '<html><body><h1>503 Service Unavailable</h1></body></html>',
                {'Content-Type': 'text/html; charset=UTF-8'},
            ),
            discovery=True,
        )

        with pytest.raises(CrczpException, match='your configuration is wrong') as exc_info:
            client.list_images()

        assert isinstance(exc_info.value.__cause__, sdk_exceptions.NotSupported)
        assert not cloud.api_calls()


class TestKeypairs:
    """nova: /os-keypairs."""

    def test_get_keypair(self, client, cloud):
        """The keypair is returned as a dict with Nova's keys."""
        cloud.route(
            'GET',
            f'{NOVA_URL}/os-keypairs/{KEYPAIR_NAME}',
            Reply(
                200,
                {
                    'keypair': {
                        'public_key': PUBLIC_KEY,
                        'name': KEYPAIR_NAME,
                        'fingerprint': FINGERPRINT,
                        'created_at': '2026-10-01T08:15:42.000000',
                        'deleted': False,
                        'deleted_at': None,
                        'updated_at': None,
                        'user_id': USER_ID,
                        'id': 4242,
                        'type': 'ssh',
                    }
                },
            ),
        )

        keypair = client.get_keypair(KEYPAIR_NAME)

        assert cloud.api_calls() == [_nova('GET', f'/os-keypairs/{KEYPAIR_NAME}')]
        assert keypair == {
            'name': KEYPAIR_NAME,
            'public_key': PUBLIC_KEY,
            'fingerprint': FINGERPRINT,
            'type': 'ssh',
            'user_id': USER_ID,
            'created_at': '2026-10-01T08:15:42.000000',
        }

    def test_get_keypair_not_found(self, client, cloud):
        """The error carries Nova's message, status and request ID, but no endpoint URL."""
        message = f'Keypair {KEYPAIR_NAME} not found for user {USER_ID}'
        cloud.route(
            'GET',
            f'{NOVA_URL}/os-keypairs/{KEYPAIR_NAME}',
            _nova_fault(404, 'itemNotFound', message),
        )

        with pytest.raises(CrczpException) as exc_info:
            client.get_keypair(KEYPAIR_NAME)

        assert str(exc_info.value) == (
            f"Failed to get keypair '{KEYPAIR_NAME}': {message} (HTTP 404) "
            f'(Request-ID: {REQUEST_ID})'
        )

    @pytest.mark.parametrize(
        'public_key, key_type, sent',
        [
            (PUBLIC_KEY, 'ssh', {'name': KEYPAIR_NAME, 'public_key': PUBLIC_KEY, 'type': 'ssh'}),
            (
                '-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n',
                'x509',
                {
                    'name': KEYPAIR_NAME,
                    'public_key': '-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n',
                    'type': 'x509',
                },
            ),
            # No public key: the key is left out, so that nova generates one.
            (None, 'ssh', {'name': KEYPAIR_NAME, 'type': 'ssh'}),
        ],
    )
    def test_create_keypair(self, client, cloud, public_key, key_type, sent):
        """The body holds the name, the type and the public key only when there is one."""

        def created(body: Any) -> Reply:
            keypair = {
                'name': body['keypair']['name'],
                'type': body['keypair']['type'],
                'public_key': body['keypair'].get('public_key', PUBLIC_KEY),
                'fingerprint': FINGERPRINT,
                'user_id': USER_ID,
            }
            if 'public_key' not in body['keypair']:
                keypair['private_key'] = '-----BEGIN OPENSSH PRIVATE KEY-----\n...'
            return Reply(201, {'keypair': keypair})

        cloud.route('POST', f'{NOVA_URL}/os-keypairs', created)

        client.create_keypair(KEYPAIR_NAME, public_key, key_type)

        assert cloud.api_calls() == [_nova('POST', '/os-keypairs', {'keypair': sent})]

    def test_create_keypair_conflict(self, client, cloud):
        """An existing keypair is reported with Nova's message."""
        cloud.route(
            'POST',
            f'{NOVA_URL}/os-keypairs',
            _nova_fault(409, 'conflictingRequest', f"Key pair '{KEYPAIR_NAME}' already exists."),
        )

        with pytest.raises(CrczpException) as exc_info:
            client.create_keypair(KEYPAIR_NAME, PUBLIC_KEY)

        assert str(exc_info.value) == (
            f"Failed to create keypair '{KEYPAIR_NAME}' with public key '{PUBLIC_KEY}': "
            f"Key pair '{KEYPAIR_NAME}' already exists. (HTTP 409) (Request-ID: {REQUEST_ID})"
        )

    def test_delete_keypair(self, client, cloud):
        """The keypair is deleted by its name."""
        cloud.route('DELETE', f'{NOVA_URL}/os-keypairs/{KEYPAIR_NAME}', Reply(204))

        client.delete_keypair(KEYPAIR_NAME)

        assert cloud.api_calls() == [_nova('DELETE', f'/os-keypairs/{KEYPAIR_NAME}')]

    def test_delete_keypair_not_found(self, client, cloud):
        """A missing keypair is an error, which sandbox-service logs as a warning."""
        message = f'Keypair {KEYPAIR_NAME} not found for user {USER_ID}'
        cloud.route(
            'DELETE',
            f'{NOVA_URL}/os-keypairs/{KEYPAIR_NAME}',
            _nova_fault(404, 'itemNotFound', message),
        )

        with pytest.raises(CrczpException) as exc_info:
            client.delete_keypair(KEYPAIR_NAME)

        assert str(exc_info.value) == (
            f"Failed to delete keypair '{KEYPAIR_NAME}': {message} (HTTP 404) "
            f'(Request-ID: {REQUEST_ID})'
        )

    def test_delete_keypair_error_page(self, client, cloud):
        """A load balancer's error page has no request ID; its text is reported."""
        cloud.route(
            'DELETE',
            f'{NOVA_URL}/os-keypairs/{KEYPAIR_NAME}',
            Reply(
                503,
                '<html><body><h1>503 Service Unavailable</h1>\n'
                'No server is available to handle this request.\n</body></html>\n',
                {'Content-Type': 'text/html', 'x-openstack-request-id': None},
            ),
        )

        with pytest.raises(CrczpException) as exc_info:
            client.delete_keypair(KEYPAIR_NAME)

        assert str(exc_info.value) == (
            f"Failed to delete keypair '{KEYPAIR_NAME}': 503 Service Unavailable: "
            'No server is available to handle this request. (HTTP 503)'
        )


class TestServers:
    """nova: /servers/{id}/action and /servers/{id}/remote-consoles."""

    @pytest.mark.parametrize(
        'method, action',
        [
            ('resume_node', {'resume': None}),
            ('start_node', {'os-start': None}),
            ('reboot_node', {'reboot': {'type': 'SOFT'}}),
        ],
    )
    def test_server_action(self, client, cloud, method, action):
        """Each action is one POST, built from the server ID without fetching the server."""
        cloud.route('POST', f'{NOVA_URL}/servers/{SERVER_ID}/action', Reply(202))

        getattr(client, method)(SERVER_ID)

        assert cloud.api_calls() == [_nova('POST', f'/servers/{SERVER_ID}/action', action)]

    def test_server_action_conflict(self, client, cloud):
        """The error carries Nova's message, status and request ID, but no endpoint URL."""
        message = f"Cannot 'resume' instance {SERVER_ID} while it is in vm_state active"
        cloud.route(
            'POST',
            f'{NOVA_URL}/servers/{SERVER_ID}/action',
            _nova_fault(409, 'conflictingRequest', message),
        )

        with pytest.raises(CrczpException) as exc_info:
            client.resume_node(SERVER_ID)

        assert str(exc_info.value) == (
            f'Failed to resume instance with id={SERVER_ID}: {message} (HTTP 409) '
            f'(Request-ID: {REQUEST_ID}).'
        )

    @pytest.mark.parametrize(
        'console_type, protocol',
        [
            ('novnc', 'vnc'),
            ('xvpvnc', 'vnc'),
            ('spice-html5', 'spice'),
            ('rdp-html5', 'rdp'),
            ('serial', 'serial'),
            ('webmks', 'mks'),
        ],
    )
    def test_get_console_url(self, client, cloud, console_type, protocol):
        """The console is created with the protocol of its type, and its URL is returned."""
        url = f'https://console.cloud.test:6082/{console_type}?token=console-token'
        cloud.route(
            'POST',
            f'{NOVA_URL}/servers/{SERVER_ID}/remote-consoles',
            Reply(
                200, {'remote_console': {'protocol': protocol, 'type': console_type, 'url': url}}
            ),
        )

        result = client.get_console_url(SERVER_ID, console_type)

        assert result == url
        assert cloud.api_calls() == [
            _nova(
                'POST',
                f'/servers/{SERVER_ID}/remote-consoles',
                {'remote_console': {'protocol': protocol, 'type': console_type}},
            )
        ]

    def test_get_console_url_not_found(self, client, cloud):
        """A console error carries only Nova's message."""
        cloud.route(
            'POST',
            f'{NOVA_URL}/servers/{SERVER_ID}/remote-consoles',
            _nova_fault(404, 'itemNotFound', f'Instance {SERVER_ID} could not be found.'),
        )

        with pytest.raises(StackException) as exc_info:
            client.get_console_url(SERVER_ID, 'spice-html5')

        assert str(exc_info.value) == (
            f'OpenStack error while getting console: Instance {SERVER_ID} could not be found.'
        )

    def test_get_console_url_unsupported_type(self, client, cloud):
        """An unknown console type is rejected without any request."""
        with pytest.raises(StackException, match='Unsupported console_type "spice-direct"'):
            client.get_console_url(SERVER_ID, 'spice-direct')

        assert not cloud.calls


def _nova_quota(limit: int, in_use: int) -> dict[str, int]:
    return {'limit': limit, 'in_use': in_use, 'reserved': 0}


def _neutron_quota(limit: int, used: int) -> dict[str, int]:
    return {'limit': limit, 'used': used, 'reserved': 0}


def _flavor(flavor_id: str, name: str, vcpus: int, ram: int, disk: int) -> dict[str, Any]:
    return {
        'id': flavor_id,
        'name': name,
        'vcpus': vcpus,
        'ram': ram,
        'disk': disk,
        'swap': '',
        'OS-FLV-EXT-DATA:ephemeral': 0,
        'OS-FLV-DISABLED:disabled': False,
        'os-flavor-access:is_public': True,
        'rxtx_factor': 1.0,
        'links': [{'rel': 'self', 'href': f'{NOVA_URL}/flavors/{flavor_id}'}],
    }


class TestProjectResources:
    """nova: /os-quota-sets, /limits, /flavors/detail; neutron: /v2.0/quotas."""

    def test_get_quota_set(self, client, cloud):
        """Limits and usage are read from the detail bodies; RAM goes from MB to GB."""
        cloud.route(
            'GET',
            f'{NOVA_URL}/os-quota-sets/{PROJECT_ID}/detail',
            Reply(
                200,
                {
                    'quota_set': {
                        'id': PROJECT_ID,
                        'cores': _nova_quota(100, 20),
                        'ram': _nova_quota(102400, 65536),
                        'instances': _nova_quota(100, 14),
                        'key_pairs': _nova_quota(100, 0),
                        'server_groups': _nova_quota(10, 0),
                        'server_group_members': _nova_quota(10, 0),
                        'metadata_items': _nova_quota(128, 0),
                        'injected_files': _nova_quota(5, 0),
                        'injected_file_content_bytes': _nova_quota(10240, 0),
                        'injected_file_path_bytes': _nova_quota(255, 0),
                        'fixed_ips': _nova_quota(-1, 0),
                        'floating_ips': _nova_quota(10, 0),
                        'security_groups': _nova_quota(10, 0),
                        'security_group_rules': _nova_quota(20, 0),
                        'networks': _nova_quota(-1, 0),
                    }
                },
            ),
        )
        cloud.route(
            'GET',
            f'{NEUTRON_URL}/v2.0/quotas/{PROJECT_ID}/details',
            Reply(
                200,
                {
                    'quota': {
                        'network': _neutron_quota(100, 13),
                        'subnet': _neutron_quota(100, 13),
                        'port': _neutron_quota(400, 49),
                        'router': _neutron_quota(10, 13),
                        'floatingip': _neutron_quota(50, 0),
                        'security_group': _neutron_quota(10, 1),
                        'security_group_rule': _neutron_quota(100, 4),
                        'rbac_policy': _neutron_quota(10, 0),
                        'subnetpool': _neutron_quota(-1, 0),
                    }
                },
            ),
        )

        quota_set = client.get_quota_set()

        assert cloud.api_calls() == [
            _nova('GET', f'/os-quota-sets/{PROJECT_ID}/detail'),
            Call('GET', f'{NEUTRON_URL}/v2.0/quotas/{PROJECT_ID}/details', {}, None, None),
        ]
        assert vars(quota_set) == vars(
            QuotaSet(
                Quota(100, 20),
                Quota(102.4, 65.5),
                Quota(100, 14),
                Quota(100, 13),
                Quota(100, 13),
                Quota(400, 49),
            )
        )

    def test_get_project_limits(self, client, cloud):
        """Nova's limits are those of the token's project; neutron's are asked by project."""
        cloud.route(
            'GET',
            f'{NOVA_URL}/limits',
            Reply(
                200,
                {
                    'limits': {
                        'rate': [],
                        'absolute': {
                            'maxTotalCores': 450,
                            'maxTotalRAMSize': 1536000,
                            'maxTotalInstances': 450,
                            'maxTotalKeypairs': 100,
                            'maxServerMeta': 128,
                            'maxImageMeta': 128,
                            'maxPersonality': 5,
                            'maxPersonalitySize': 10240,
                            'maxServerGroups': 10,
                            'maxServerGroupMembers': 10,
                            'maxSecurityGroups': 10,
                            'maxSecurityGroupRules': 20,
                            'maxTotalFloatingIps': 10,
                            'totalCoresUsed': 5,
                            'totalRAMUsed': 34816,
                            'totalInstancesUsed': 2,
                            'totalServerGroupsUsed': 0,
                            'totalSecurityGroupsUsed': 1,
                            'totalFloatingIpsUsed': 0,
                        },
                    }
                },
            ),
        )
        cloud.route(
            'GET',
            f'{NEUTRON_URL}/v2.0/quotas/{PROJECT_ID}',
            Reply(
                200,
                {
                    'quota': {
                        'network': 900,
                        'subnet': 900,
                        'port': 3600,
                        'router': 10,
                        'floatingip': 50,
                        'security_group': 10,
                        'security_group_rule': 100,
                        'rbac_policy': 10,
                        'subnetpool': -1,
                    }
                },
            ),
        )

        limits = client.get_project_limits()

        assert cloud.api_calls() == [
            _nova('GET', '/limits'),
            Call('GET', f'{NEUTRON_URL}/v2.0/quotas/{PROJECT_ID}', {}, None, None),
        ]
        assert limits == Limits(
            vcpu=450, ram=1536.0, instances=450, network=900, subnet=900, port=3600
        )

    def test_get_flavors_dict_follows_next_links(self, client, cloud):
        """Only public flavors are asked for, on every page; RAM goes from MB to GB."""
        cloud.route(
            'GET',
            f'{NOVA_URL}/flavors/detail?is_public=True',
            Reply(
                200,
                {
                    'flavors': [
                        _flavor('3c8a4e2b', 'a1-2-20', 1, 2048, 20),
                        _flavor('5d2f7a9c', 'a2-4-35', 2, 4096, 35),
                    ],
                    'flavors_links': [
                        {
                            'rel': 'next',
                            'href': f'{NOVA_URL}/flavors/detail?is_public=True&marker=5d2f7a9c',
                        }
                    ],
                },
            ),
        )
        cloud.route(
            'GET',
            f'{NOVA_URL}/flavors/detail?is_public=True&marker=5d2f7a9c',
            Reply(200, {'flavors': [_flavor('7e1b3d5f', 'a16-32-80', 16, 32768, 80)]}),
        )

        flavors = client.get_flavors_dict()

        assert cloud.api_calls() == [
            _nova('GET', '/flavors/detail', is_public=['True']),
            _nova('GET', '/flavors/detail', is_public=['True'], marker=['5d2f7a9c']),
        ]
        assert flavors == {
            'a1-2-20': {'vcpu': 1, 'ram': 2.0},
            'a2-4-35': {'vcpu': 2, 'ram': 4.1},
            'a16-32-80': {'vcpu': 16, 'ram': 32.8},
        }


STACK = 'default0-p0000000001-s0000000001'
STACK_NAMES = StackNameScheme('default0')


def _server(server_id: str, name: str, flavor_id: str) -> dict[str, Any]:
    """A server of the detailed listing, as Nova sends it at microversion 2.10."""
    return {
        'id': server_id,
        'name': name,
        'status': 'ACTIVE',
        'tenant_id': PROJECT_ID,
        'user_id': USER_ID,
        'metadata': {},
        'hostId': 'a1b2c3d4e5f6',
        'image': {'id': '7c1d4e5a-3b6f-4e2a-9d8c-1f0a2b3c4d5e', 'links': []},
        'flavor': {'id': flavor_id, 'links': []},
        'created': '2026-10-30T08:00:00Z',
        'updated': '2026-10-30T08:01:10Z',
        'addresses': {},
        'accessIPv4': '',
        'accessIPv6': '',
        'links': [{'rel': 'self', 'href': f'{NOVA_URL}/servers/{server_id}'}],
        'OS-DCF:diskConfig': 'MANUAL',
        'key_name': None,
        'OS-EXT-AZ:availability_zone': 'nova',
        'config_drive': '',
        'os-extended-volumes:volumes_attached': [],
    }


def _neutron(resource_id: str, name: str, tags: list[str], **fields: Any) -> dict[str, Any]:
    """A Neutron resource with the attributes every resource carries."""
    return {
        'id': resource_id,
        'name': name,
        'tenant_id': PROJECT_ID,
        'project_id': PROJECT_ID,
        'description': '',
        'tags': tags,
        'created_at': '2026-10-30T08:00:00Z',
        'updated_at': '2026-10-30T08:00:05Z',
        'revision_number': 2,
        **fields,
    }


def _neutron_error(status: int, error_type: str, message: str) -> Reply:
    return Reply(status, {'NeutronError': {'type': error_type, 'message': message, 'detail': ''}})


class TestStackResources:
    """nova: /servers, /flavors; neutron: /v2.0/{networks,subnets,ports,routers,floatingips}
    and /v2.0/taas/tap_mirrors."""

    @staticmethod
    def _route_project(cloud: FakeCloud, tap_mirrors: Reply | None = None) -> None:
        """Answer the listings with one stack's resources and a server of the base project."""
        cloud.route(
            'GET',
            f'{NOVA_URL}/servers/detail',
            Reply(
                200,
                {
                    'servers': [
                        _server('srv-1', f'{STACK}-server', 'f-small'),
                        _server('srv-4', 'base-server', 'f-large'),
                    ]
                },
            ),
        )
        cloud.route(
            'GET',
            f'{NOVA_URL}/flavors/detail?is_public=True',
            Reply(200, {'flavors': [_flavor('f-small', 'a1-2-20', 1, 2048, 20)]}),
        )
        neutron = f'{NEUTRON_URL}/v2.0'
        listings = {
            'networks': [_neutron('net-1', f'{STACK}-switch', [STACK], status='ACTIVE')],
            'subnets': [_neutron('sub-1', f'{STACK}-switch-subnet', [], cidr='10.10.20.0/24')],
            'ports': [_neutron('port-1', f'{STACK}-link-1', [STACK], device_owner='')],
            'routers': [_neutron('rtr-1', f'{STACK}-tapm-rtr', [STACK], status='ACTIVE')],
            'floatingips': [
                _neutron('fip-1', '', [STACK], floating_ip_address='10.0.0.5', port_id=None)
            ],
        }
        for collection, items in listings.items():
            cloud.route('GET', f'{neutron}/{collection}', Reply(200, {collection: items}))
        cloud.route(
            'GET',
            f'{neutron}/taas/tap_mirrors',
            tap_mirrors or Reply(200, {'tap_mirrors': [_neutron('tm-1', f'{STACK}-tapm-0', [])]}),
        )

    def test_list_stack_inventories(self, client, cloud):
        """Every listing is one request, compute ones at the pinned microversion."""
        self._route_project(cloud)

        inventories = client.list_stack_inventories(STACK_NAMES)

        neutron = f'{NEUTRON_URL}/v2.0'
        assert cloud.api_calls() == [
            _nova('GET', '/servers/detail'),
            *(
                Call('GET', f'{neutron}/{collection}', {}, None, None)
                for collection in ('networks', 'subnets', 'ports', 'routers', 'floatingips')
            ),
            Call('GET', f'{neutron}/taas/tap_mirrors', {}, None, None),
            _nova('GET', '/flavors/detail', is_public=['True']),
        ]
        assert list(inventories) == [STACK]
        assert sorted(
            (resource.kind, resource.resource_id, resource.name)
            for resource in inventories[STACK].resources
        ) == [
            ('floating_ip', 'fip-1', '10.0.0.5'),
            ('network', 'net-1', f'{STACK}-switch'),
            ('port', 'port-1', f'{STACK}-link-1'),
            ('router', 'rtr-1', f'{STACK}-tapm-rtr'),
            ('server', 'srv-1', f'{STACK}-server'),
            ('subnet', 'sub-1', f'{STACK}-switch-subnet'),
            ('tap_mirror', 'tm-1', f'{STACK}-tapm-0'),
        ]
        assert inventories[STACK].ram_gb == 2.0

    def test_list_without_the_taas_extension(self, client, cloud):
        """Neutron without TaaS answers 404 for tap mirrors; the rest is still listed."""
        self._route_project(
            cloud,
            tap_mirrors=Reply(
                404,
                '404 Not Found\n\nThe resource could not be found.\n\n',
                {'Content-Type': 'text/plain'},
            ),
        )

        inventories = client.list_stack_inventories(STACK_NAMES)

        kinds = {resource.kind for resource in inventories[STACK].resources}
        assert sorted(kinds) == ['floating_ip', 'network', 'port', 'router', 'server', 'subnet']

    def test_delete_stack_resources(self, client, cloud):
        """Dependents go first; a router loses its interfaces and gateway before it is deleted."""
        self._route_project(cloud)
        neutron = f'{NEUTRON_URL}/v2.0'
        for path in ('taas/tap_mirrors/tm-1', 'floatingips/fip-1', 'routers/rtr-1'):
            cloud.route('DELETE', f'{neutron}/{path}', Reply(204))
        for path in ('ports/port-1', 'subnets/sub-1', 'networks/net-1'):
            cloud.route('DELETE', f'{neutron}/{path}', Reply(204))
        cloud.route('DELETE', f'{NOVA_URL}/servers/srv-1', Reply(204))
        cloud.route(
            'GET',
            f'{NOVA_URL}/servers/srv-1',
            _nova_fault(404, 'itemNotFound', 'Instance srv-1 could not be found.'),
        )
        interface = _neutron('iface-1', '', [], device_owner='network:router_interface')
        cloud.route('GET', f'{neutron}/ports?device_id=rtr-1', Reply(200, {'ports': [interface]}))
        cloud.route(
            'PUT',
            f'{neutron}/routers/rtr-1/remove_router_interface',
            Reply(200, {'id': 'rtr-1', 'port_id': 'iface-1', 'subnet_id': 'sub-1'}),
        )
        cloud.route(
            'PUT',
            f'{neutron}/routers/rtr-1',
            Reply(200, {'router': _neutron('rtr-1', f'{STACK}-tapm-rtr', [STACK])}),
        )

        results = client.delete_stack_resources(STACK, STACK_NAMES)

        assert [result.outcome.value for result in results] == ['deleted'] * 7
        # After the listings of servers and of the six kinds of Neutron resources.
        deletions = cloud.api_calls()[7:]
        assert deletions == [
            Call('DELETE', f'{neutron}/taas/tap_mirrors/tm-1', {}, None, None),
            Call('DELETE', f'{neutron}/floatingips/fip-1', {}, None, None),
            _nova('DELETE', '/servers/srv-1'),
            _nova('GET', '/servers/srv-1'),
            Call('GET', f'{neutron}/ports', {'device_id': ['rtr-1']}, None, None),
            Call(
                'PUT',
                f'{neutron}/routers/rtr-1/remove_router_interface',
                {},
                {'port_id': 'iface-1'},
                None,
            ),
            Call(
                'PUT',
                f'{neutron}/routers/rtr-1',
                {},
                {'router': {'external_gateway_info': {}}},
                None,
            ),
            Call('DELETE', f'{neutron}/routers/rtr-1', {}, None, None),
            Call('DELETE', f'{neutron}/ports/port-1', {}, None, None),
            Call('DELETE', f'{neutron}/subnets/sub-1', {}, None, None),
            Call('DELETE', f'{neutron}/networks/net-1', {}, None, None),
        ]

    def test_failed_deletion_reports_neutrons_message(self, client, cloud):
        """A failure keeps Neutron's message, status and request ID, but no endpoint URL."""
        self._route_project(cloud)
        neutron = f'{NEUTRON_URL}/v2.0'
        message = (
            'Unable to complete operation on network net-1. '
            'There are one or more ports still in use on the network.'
        )
        for path in ('taas/tap_mirrors/tm-1', 'floatingips/fip-1', 'ports/port-1'):
            cloud.route('DELETE', f'{neutron}/{path}', Reply(204))
        for path in ('routers/rtr-1', 'subnets/sub-1'):
            cloud.route('DELETE', f'{neutron}/{path}', _neutron_error(404, 'NotFound', 'gone'))
        cloud.route(
            'DELETE', f'{neutron}/networks/net-1', _neutron_error(409, 'NetworkInUse', message)
        )
        cloud.route('DELETE', f'{NOVA_URL}/servers/srv-1', _nova_fault(404, 'itemNotFound', 'gone'))
        cloud.route('GET', f'{neutron}/ports?device_id=rtr-1', Reply(200, {'ports': []}))
        cloud.route(
            'PUT', f'{neutron}/routers/rtr-1', _neutron_error(404, 'RouterNotFound', 'gone')
        )

        results = {
            result.resource.resource_id: result
            for result in client.delete_stack_resources(STACK, STACK_NAMES)
        }

        assert results['net-1'].error == f'{message} (HTTP 409) (Request-ID: {REQUEST_ID})'
        assert {resource_id: result.outcome.value for resource_id, result in results.items()} == {
            'tm-1': 'deleted',
            'fip-1': 'deleted',
            'srv-1': 'already_gone',
            'rtr-1': 'already_gone',
            'port-1': 'deleted',
            'sub-1': 'already_gone',
            'net-1': 'failed',
        }


class TestAuthentication:
    """keystone: POST /v3/auth/tokens."""

    def test_unauthorized_is_crczp_exception(self, client, cloud):
        """A rejected application credential is reported as a configuration error."""
        cloud.route(
            'POST',
            f'{AUTH_URL}/auth/tokens',
            Reply(
                401,
                {
                    'error': {
                        'code': 401,
                        'message': 'The request you have made requires authentication.',
                        'title': 'Unauthorized',
                    }
                },
            ),
        )

        with pytest.raises(CrczpException, match='your configuration is wrong'):
            client.get_project_limits()

        assert not cloud.api_calls()

    def test_token_request(self, client, cloud):
        """The session authenticates with the application credential."""
        assert client.get_project_name() == 'crczp-devel'

        assert cloud.calls == [
            Call(
                'POST',
                f'{AUTH_URL}/auth/tokens',
                {},
                {
                    'auth': {
                        'identity': {
                            'methods': ['application_credential'],
                            'application_credential': {
                                'id': 'app-cred-id',
                                'secret': 'app-cred-secret',
                            },
                        }
                    }
                },
                None,
            )
        ]
