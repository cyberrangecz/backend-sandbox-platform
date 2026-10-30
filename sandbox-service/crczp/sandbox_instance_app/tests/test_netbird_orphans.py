"""Tests for NetBird listing and deletion by short stack name."""

# pylint: disable=redefined-outer-name

from typing import Any
from unittest.mock import MagicMock, call

import pytest

from crczp.cloud_commons import StackNameScheme
from crczp.sandbox_common_lib.netbird_client import NetbirdApiError, NetbirdClient
from crczp.sandbox_instance_app.lib.netbird_orphans import (
    NetbirdObject,
    NetbirdStackObjects,
    delete_netbird_stack_objects,
    list_netbird_objects_by_stack,
)

STACK_NAMES = StackNameScheme('x')
STACK_P1_S1 = 'x-p0000000001-s0000000001'
STACK_P1_S12 = 'x-p0000000001-s0000000012'
STACK_P2_S3 = 'x-p0000000002-s0000000003'


@pytest.fixture
def client() -> MagicMock:
    """A client double whose listings are empty until a test fills them."""
    fake = MagicMock(spec=NetbirdClient)
    fake.list_groups.return_value = []
    fake.list_setup_keys.return_value = []
    fake.list_routes.return_value = []
    fake.list_policies.return_value = []
    fake.list_nameserver_groups.return_value = []
    return fake


def ids(objects: list[NetbirdObject]) -> list[str]:
    """The object ids of a list, in order."""
    return [item.object_id for item in objects]


class TestListByStack:
    """Tests for grouping listed objects by exact short stack name."""

    def test_s1_does_not_absorb_s12(self, client: MagicMock):
        """Objects of x-p1-s1 and x-p1-s12 land under separate stacks, keyed by full name."""
        client.list_groups.return_value = [
            {'id': 'g1', 'name': 'x-p1-s1-access'},
            {'id': 'g2', 'name': 'x-p1-s12-access'},
            {'id': 'g3', 'name': 'x-p1-s1-host1'},
        ]
        client.list_policies.return_value = [{'id': 'p2', 'name': 'x-p1-s12-host1-policy'}]

        stacks = list_netbird_objects_by_stack(client, STACK_NAMES)

        assert set(stacks) == {STACK_P1_S1, STACK_P1_S12}
        assert ids(stacks[STACK_P1_S1].groups) == ['g1', 'g3']
        assert ids(stacks[STACK_P1_S12].groups) == ['g2']
        assert ids(stacks[STACK_P1_S12].policies) == ['p2']
        assert stacks[STACK_P1_S1].policies == []

    def test_every_kind_is_assigned(self, client: MagicMock):
        """Setup keys, routes, policies and nameserver groups are all grouped."""
        client.list_setup_keys.return_value = [
            {'id': 7, 'name': 'x-p1-s1-access'},
            {'id': 8, 'name': 'x-p1-s1-host1-host'},
        ]
        client.list_routes.return_value = [
            {
                'id': 'r1',
                'network_id': 'x-p1-s1-host1-10-0-0-0-24',
                'description': 'x-p1-s1 host1 route 10.0.0.0/24',
            }
        ]
        client.list_policies.return_value = [{'id': 'p1', 'name': 'x-p1-s1-host1-policy'}]
        client.list_nameserver_groups.return_value = [{'id': 'n1', 'name': 'x-p1-s1-dns'}]

        stack = list_netbird_objects_by_stack(client, STACK_NAMES)[STACK_P1_S1]

        assert ids(stack.setup_keys) == ['7', '8']
        assert ids(stack.routes) == ['r1']
        assert ids(stack.policies) == ['p1']
        assert ids(stack.nameserver_groups) == ['n1']

    def test_route_falls_back_to_network_id(self, client: MagicMock):
        """A route without a usable description is matched by its network id."""
        client.list_routes.return_value = [
            {'id': 'r1', 'network_id': 'x-p2-s3-host1-10-0-0-0-24', 'description': ''},
            {'id': 'r2', 'network_id': 'x-p2-s3', 'description': 'unrelated'},
            {'id': 'r3', 'network_id': 'x-p2-s3-host2-10-0-1-0-24', 'description': 'x-p2-s3 route'},
        ]

        stacks = list_netbird_objects_by_stack(client, STACK_NAMES)

        assert ids(stacks[STACK_P2_S3].routes) == ['r1', 'r3']

    def test_unrelated_names_are_left_out(self, client: MagicMock):
        """Names without a short stack name of the deployment and separator belong to no stack."""
        client.list_groups.return_value = [
            {'id': 'g1', 'name': 'All'},
            {'id': 'g2', 'name': 'x-p1-s1'},
            {'id': 'g3', 'name': 'x-p1-sx-access'},
            {'id': 'g4', 'name': 'x-p1-s1extra-access'},
            {'id': 'g5', 'name': 'other-p1-s1-access'},
            {'id': 'g6', 'name': 'y-x-p1-s1-access'},
            {'id': 'g7', 'name': 'x-p01-s1-access'},
        ]
        client.list_routes.return_value = [
            {
                'id': 'r1',
                'network_id': 'other',
                'description': 'other-p1-s1 host1 route 10.0.0.0/24',
            }
        ]

        assert list_netbird_objects_by_stack(client, STACK_NAMES) == {}

    def test_a_prefixed_name_without_a_stack_is_logged_as_an_error(
        self, client: MagicMock, caplog: pytest.LogCaptureFixture
    ):
        """A name with the deployment prefix but no parsable ids is reported; others are not."""
        client.list_groups.return_value = [
            {'id': 'g1', 'name': 'x-p1-sx-access'},
            {'id': 'g2', 'name': 'other-p1-s1-access'},
            {'id': 'g3', 'name': 'All'},
        ]
        client.list_policies.return_value = [{'id': 'p1', 'name': 'x-broken'}]

        with caplog.at_level('ERROR'):
            assert list_netbird_objects_by_stack(client, STACK_NAMES) == {}

        logged = [record.getMessage() for record in caplog.records]
        assert len(logged) == 2
        assert "'x-p1-sx-access'" in logged[0]
        assert "'x-broken'" in logged[1]


class TestDeleteStackObjects:
    """Tests for the combined teardown of one stack."""

    @pytest.fixture
    def objects(self) -> NetbirdStackObjects:
        """One object of every kind."""
        return NetbirdStackObjects(
            groups=[NetbirdObject('group', 'g1', 'x-p1-s1-access')],
            setup_keys=[NetbirdObject('setup_key', 'k1', 'x-p1-s1-access')],
            routes=[NetbirdObject('route', 'r1', 'x-p1-s1-h')],
            policies=[NetbirdObject('policy', 'p1', 'x-p1-s1-h-policy')],
            nameserver_groups=[NetbirdObject('nameserver_group', 'n1', 'x-p1-s1-dns')],
        )

    def test_deletes_in_teardown_order(self, client: MagicMock, objects: NetbirdStackObjects):
        """Policies and routes go first, peers before their group, groups last."""
        client.list_group_peer_ids.return_value = ['peer1']
        manager = MagicMock()
        manager.attach_mock(client, 'client')

        results = delete_netbird_stack_objects(client, objects)

        assert [c for c in manager.mock_calls if c[0].startswith('client.delete')] == [
            call.client.delete_policy('p1'),
            call.client.delete_route('r1'),
            call.client.delete_nameserver_group('n1'),
            call.client.delete_setup_key('k1'),
            call.client.delete_peer('peer1'),
            call.client.delete_group('g1'),
        ]
        assert [r.kind for r in results] == [
            'policy',
            'route',
            'nameserver_group',
            'setup_key',
            'peer',
            'group',
        ]
        assert all(r.error is None for r in results)

    def test_failure_is_reported_and_others_proceed(
        self, client: MagicMock, objects: NetbirdStackObjects
    ):
        """An API error on one object is recorded and the rest are still deleted."""
        client.list_group_peer_ids.return_value = []
        client.delete_route.side_effect = NetbirdApiError('DELETE', 'u', 500, 'boom')

        results = delete_netbird_stack_objects(client, objects)

        failed = [r for r in results if r.error is not None]
        assert [(r.kind, r.object_id) for r in failed] == [('route', 'r1')]
        client.delete_group.assert_called_once_with('g1')

    def test_group_with_unlistable_peers_is_kept(
        self, client: MagicMock, objects: NetbirdStackObjects
    ):
        """A group whose peers cannot be listed is reported and not deleted."""
        client.list_group_peer_ids.side_effect = NetbirdApiError('GET', 'u', 500, 'boom')

        results = delete_netbird_stack_objects(client, objects)

        client.delete_group.assert_not_called()
        assert any(r.kind == 'group' and r.error for r in results)


class TestClientListing:
    """Tests that the client list methods return the bare JSON arrays."""

    @pytest.mark.parametrize(
        ('method', 'path'),
        [
            ('list_groups', '/api/groups'),
            ('list_setup_keys', '/api/setup-keys'),
            ('list_routes', '/api/routes'),
            ('list_policies', '/api/policies'),
            ('list_nameserver_groups', '/api/dns/nameservers'),
        ],
    )
    def test_list_gets_collection(self, mocker: Any, method: str, path: str):
        """Each list method GETs its collection path and returns the array."""
        session = MagicMock()
        mocker.patch(
            'crczp.sandbox_common_lib.netbird_client.http_requests.Session', return_value=session
        )
        response = MagicMock(status_code=200)
        response.json.return_value = [{'id': 'a'}]
        session.request.return_value = response

        result = getattr(NetbirdClient('https://nb.example.com/', 'pat'), method)()

        assert result == [{'id': 'a'}]
        assert session.request.call_args.args[:2] == ('GET', f'https://nb.example.com{path}')
