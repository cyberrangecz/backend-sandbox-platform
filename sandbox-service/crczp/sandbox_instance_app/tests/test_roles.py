"""Tests for role grants: the PoolRoleGrant model, resolution, and the grant API."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError
from rest_framework.reverse import reverse
from rest_framework.test import APIRequestFactory

from crczp.cloud_commons import UNIVERSAL_ROLES
from crczp.sandbox_definition_app.views import DefinitionRolesView
from crczp.sandbox_instance_app.lib import roles
from crczp.sandbox_instance_app.models import Pool, PoolRoleGrant
from crczp.sandbox_instance_app.views import PoolRolesView

pytestmark = pytest.mark.django_db


class TestPoolRoleGrantModel:
    """Tests for the PoolRoleGrant model's constraints."""

    def test_unique_together_pool_user_role(self, pool):
        """A duplicate (pool, user, role) grant is rejected."""
        PoolRoleGrant.objects.create(pool=pool, user=42, role='red-team')
        with pytest.raises(IntegrityError):
            PoolRoleGrant.objects.create(pool=pool, user=42, role='red-team')

    def test_same_user_can_hold_several_roles(self, pool):
        """A user holds a set of roles in a pool as several distinct rows."""
        PoolRoleGrant.objects.create(pool=pool, user=42, role='red-team')
        PoolRoleGrant.objects.create(pool=pool, user=42, role='blue-team')
        assert PoolRoleGrant.objects.filter(pool=pool, user=42).count() == 2

    def test_deleting_pool_cascades_to_grants(self, pool):
        """Deleting a pool removes its grants."""
        PoolRoleGrant.objects.create(pool=pool, user=42, role='red-team')
        pool_id = pool.id
        pool.delete()
        assert not PoolRoleGrant.objects.filter(pool_id=pool_id).exists()


class TestResolveUsersRoles:
    """Tests for resolve_users_roles."""

    @pytest.fixture
    def request_stub(self):
        """A minimal request-like object carrying only what resolve_users_roles reads."""
        request = APIRequestFactory().get('/')
        request.user = AnonymousUser()
        return request

    def test_organizer_resolves_to_universal_roles(self, mocker, request_stub, pool):
        """A caller holding the organiser system role resolves to every role there is."""
        mocker.patch(
            'crczp.sandbox_instance_app.lib.roles.OrganizerPermission.has_permission',
            return_value=True,
        )
        mocker.patch(
            'crczp.sandbox_instance_app.lib.roles.AdminPermission.has_permission',
            return_value=False,
        )
        assert roles.resolve_users_roles(request_stub, pool) is UNIVERSAL_ROLES

    def test_admin_resolves_to_universal_roles(self, mocker, request_stub, pool):
        """A caller holding the admin system role resolves to every role there is."""
        mocker.patch(
            'crczp.sandbox_instance_app.lib.roles.OrganizerPermission.has_permission',
            return_value=False,
        )
        mocker.patch(
            'crczp.sandbox_instance_app.lib.roles.AdminPermission.has_permission',
            return_value=True,
        )
        assert roles.resolve_users_roles(request_stub, pool) is UNIVERSAL_ROLES

    def test_ordinary_user_resolves_from_the_store(self, mocker, request_stub, pool):
        """An ordinary user resolves to exactly the roles granted to them in that pool."""
        mocker.patch(
            'crczp.sandbox_instance_app.lib.roles.OrganizerPermission.has_permission',
            return_value=False,
        )
        mocker.patch(
            'crczp.sandbox_instance_app.lib.roles.AdminPermission.has_permission',
            return_value=False,
        )
        request_stub.user.uag_user_id = 42
        PoolRoleGrant.objects.create(pool=pool, user=42, role='red-team')
        PoolRoleGrant.objects.create(pool=pool, user=42, role='blue-team')

        assert roles.resolve_users_roles(request_stub, pool) == frozenset({'red-team', 'blue-team'})

    def test_grants_in_another_pool_do_not_carry_over(self, mocker, request_stub, pool, definition):
        """Grants held in a different pool are not part of this pool's resolution."""
        other_pool = Pool.objects.create(
            definition=definition,
            max_size=1,
            private_management_key='-----RSA PRIVATE KEY-----',
            public_management_key='ssh-rsa',
            uuid='other-pool-uuid',
        )
        mocker.patch(
            'crczp.sandbox_instance_app.lib.roles.OrganizerPermission.has_permission',
            return_value=False,
        )
        mocker.patch(
            'crczp.sandbox_instance_app.lib.roles.AdminPermission.has_permission',
            return_value=False,
        )
        request_stub.user.uag_user_id = 42
        PoolRoleGrant.objects.create(pool=other_pool, user=42, role='red-team')

        assert roles.resolve_users_roles(request_stub, pool) == frozenset()

    def test_user_with_no_grant_resolves_to_empty_set(self, mocker, request_stub, pool):
        """A requesting user holding no grant in the pool resolves to the empty set."""
        mocker.patch(
            'crczp.sandbox_instance_app.lib.roles.OrganizerPermission.has_permission',
            return_value=False,
        )
        mocker.patch(
            'crczp.sandbox_instance_app.lib.roles.AdminPermission.has_permission',
            return_value=False,
        )
        request_stub.user.uag_user_id = 99

        assert roles.resolve_users_roles(request_stub, pool) == frozenset()


class TestPoolRolesView:
    """Tests for the pools/<id>/roles endpoint."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker):  # pylint: disable=attribute-defined-outside-init
        """Every grant is privileged (organiser) and every definition is unreadable by
        default; individual tests override the declared-roles resolution as needed."""
        mocker.patch(
            'crczp.sandbox_instance_app.lib.roles.OrganizerPermission.has_permission',
            return_value=True,
        )
        mocker.patch(
            'crczp.sandbox_instance_app.lib.roles.AdminPermission.has_permission',
            return_value=False,
        )
        self.factory = APIRequestFactory()

    def _get(self, pool):
        request = self.factory.get(reverse('pool-roles', kwargs={'pool_id': pool.id}))
        request.user = AnonymousUser()
        return PoolRolesView.as_view()(request, pool_id=pool.id)

    def _post(self, pool, data):
        request = self.factory.post(
            reverse('pool-roles', kwargs={'pool_id': pool.id}), data, format='json'
        )
        request.user = AnonymousUser()
        return PoolRolesView.as_view()(request, pool_id=pool.id)

    def test_get_reads_only_granted_users(self, pool):
        """A user holding no role is absent from the response."""
        PoolRoleGrant.objects.create(pool=pool, user=42, role='red-team')
        response = self._get(pool)
        assert response.data == {'42': ['red-team']}

    def test_get_empty_pool_reads_empty(self, pool):
        """A pool with no grants reads as an empty object."""
        response = self._get(pool)
        assert response.data == {}

    def test_post_replaces_named_users_grants(self, mocker, pool):
        """A named user's grants are replaced, not extended."""
        mocker.patch(
            'crczp.sandbox_instance_app.views._get_pool_declared_roles',
            return_value={'red-team', 'blue-team'},
        )
        PoolRoleGrant.objects.create(pool=pool, user=42, role='red-team')
        response = self._post(pool, {'42': ['blue-team']})
        assert response.status_code == 200
        assert set(
            PoolRoleGrant.objects.filter(pool=pool, user=42).values_list('role', flat=True)
        ) == {'blue-team'}

    def test_post_empty_list_removes_grants(self, mocker, pool):
        """An empty list removes a user's grants."""
        mocker.patch(
            'crczp.sandbox_instance_app.views._get_pool_declared_roles', return_value=set()
        )
        PoolRoleGrant.objects.create(pool=pool, user=42, role='red-team')
        self._post(pool, {'42': []})
        assert not PoolRoleGrant.objects.filter(pool=pool, user=42).exists()

    def test_post_leaves_other_users_untouched(self, mocker, pool):
        """A user absent from the body is untouched."""
        mocker.patch(
            'crczp.sandbox_instance_app.views._get_pool_declared_roles',
            return_value={'red-team', 'blue-team'},
        )
        PoolRoleGrant.objects.create(pool=pool, user=42, role='red-team')
        PoolRoleGrant.objects.create(pool=pool, user=43, role='blue-team')
        self._post(pool, {'42': ['blue-team']})
        assert set(
            PoolRoleGrant.objects.filter(pool=pool, user=43).values_list('role', flat=True)
        ) == {'blue-team'}

    def test_post_rejects_undeclared_role_naming_every_pairing(self, mocker, pool):
        """The call is rejected in full, naming every offending pairing."""
        mocker.patch(
            'crczp.sandbox_instance_app.views._get_pool_declared_roles', return_value={'red-team'}
        )
        response = self._post(pool, {'42': ['ghost-team'], '43': ['red-team', 'other-ghost']})
        assert response.status_code == 400
        undeclared_pairs = {(item['user'], item['role']) for item in response.data['undeclared']}
        assert undeclared_pairs == {(42, 'ghost-team'), (43, 'other-ghost')}
        # nothing was written, the valid pairing included
        assert not PoolRoleGrant.objects.filter(pool=pool).exists()

    def test_post_proceeds_when_definition_is_unreadable(self, mocker, pool):
        """An unreadable definition skips validation and the write proceeds."""
        mocker.patch('crczp.sandbox_instance_app.views._get_pool_declared_roles', return_value=None)
        response = self._post(pool, {'42': ['anything-goes']})
        assert response.status_code == 200
        assert PoolRoleGrant.objects.filter(pool=pool, user=42, role='anything-goes').exists()

    def test_post_response_carries_the_pool_grants_after_the_call(self, mocker, pool):
        """The response carries the pool's grants after the call, no further read needed."""
        mocker.patch(
            'crczp.sandbox_instance_app.views._get_pool_declared_roles', return_value={'red-team'}
        )
        response = self._post(pool, {'42': ['red-team']})
        assert response.data == {'42': ['red-team']}


class TestDefinitionRolesView:
    """Tests for the definitions/<id>/roles endpoint."""

    @pytest.fixture(autouse=True)
    def set_up(self, mocker):  # pylint: disable=attribute-defined-outside-init
        mocker.patch(
            'crczp.sandbox_definition_app.views.DesignerPermission.has_permission',
            return_value=True,
        )
        mocker.patch(
            'crczp.sandbox_definition_app.views.OrganizerPermission.has_permission',
            return_value=False,
        )
        mocker.patch(
            'crczp.sandbox_definition_app.views.AdminPermission.has_permission',
            return_value=False,
        )
        self.factory = APIRequestFactory()

    def test_returns_rev_rev_sha_and_roles(self, mocker, definition):
        """The response reports rev, the commit it resolved to, and the declared roles."""
        mock_provider = mocker.patch(
            'crczp.sandbox_definition_app.views.definitions.get_def_provider'
        )
        mock_provider.return_value.get_rev_sha.return_value = 'abcdef0'
        mock_topology_definition = mocker.MagicMock()
        mock_topology_definition.get_declared_roles.return_value = {'blue-team', 'red-team'}
        mocker.patch(
            'crczp.sandbox_definition_app.views.definitions.get_definition',
            return_value=mock_topology_definition,
        )

        request = self.factory.get(
            reverse('definition-roles', kwargs={'definition_id': definition.id})
        )
        request.user = AnonymousUser()
        response = DefinitionRolesView.as_view()(request, definition_id=definition.id)

        assert response.data == {
            'rev': definition.rev,
            'rev_sha': 'abcdef0',
            'roles': ['blue-team', 'red-team'],
        }

    def test_rev_query_param_overrides_the_stored_revision(self, mocker, definition):
        """Passing ?rev= resolves that revision instead of the definition's stored one."""
        mock_provider = mocker.patch(
            'crczp.sandbox_definition_app.views.definitions.get_def_provider'
        )
        mock_provider.return_value.get_rev_sha.return_value = 'sha-of-other-branch'
        mock_topology_definition = mocker.MagicMock()
        mock_topology_definition.get_declared_roles.return_value = set()
        mocker.patch(
            'crczp.sandbox_definition_app.views.definitions.get_definition',
            return_value=mock_topology_definition,
        )

        request = self.factory.get(
            reverse('definition-roles', kwargs={'definition_id': definition.id}),
            {'rev': 'other-branch'},
        )
        request.user = AnonymousUser()
        response = DefinitionRolesView.as_view()(request, definition_id=definition.id)

        assert response.data['rev'] == 'other-branch'
        mock_provider.return_value.get_rev_sha.assert_called_once_with('other-branch')
