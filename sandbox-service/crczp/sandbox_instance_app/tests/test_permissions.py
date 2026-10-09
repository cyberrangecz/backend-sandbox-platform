"""Tests for the trainee self-service permissions in crczp.sandbox_uag.permissions."""

from types import SimpleNamespace

import pytest
from django.contrib.auth import models as auth_models
from django.contrib.auth.models import AnonymousUser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from crczp.sandbox_instance_app.models import SandboxAllocationUnit
from crczp.sandbox_instance_app.tests.conftest import AccessLevel, authenticate, make_user
from crczp.sandbox_uag import permissions

pytestmark = pytest.mark.django_db

UNIT_VIEW = SimpleNamespace(
    queryset=SandboxAllocationUnit.objects.all(), trainee_self_service_methods={'GET'}
)


def drf_request(method: str, user=None, **kwargs) -> Request:
    """Build a DRF request, authenticated as the user if one is given."""
    request = getattr(APIRequestFactory(), method)('/', **kwargs)
    if user is not None:
        authenticate(request, user)
    return Request(request)


class TestGetCallerSub:
    """The caller's OIDC sub comes from the authenticated identity only."""

    def test_sub_comes_from_the_userinfo(self, trainee):
        """The userinfo the token was verified with names the caller."""
        assert permissions.get_caller_sub(drf_request('get', trainee)) == 'trainee-sub'

    def test_body_cannot_choose_the_sub(self, trainee):
        """A created_by_sub in the request body is not the caller's identity."""
        request = drf_request('post', trainee, data={'created_by_sub': 'victim'}, format='json')

        assert permissions.get_caller_sub(request) == 'trainee-sub'

    def test_falls_back_to_the_username(self):
        """Without userinfo, the sub is the username before the issuer; it may contain '|'."""
        user = auth_models.User.objects.create(username='auth0|123|https://issuer.test')
        raw = APIRequestFactory().get('/')
        force_authenticate(raw, user=user)

        assert permissions.get_caller_sub(Request(raw)) == 'auth0|123'

    def test_username_without_issuer_has_no_sub(self):
        """A local account without an OIDC identity has no sub."""
        raw = APIRequestFactory().get('/')
        force_authenticate(raw, user=auth_models.User.objects.create(username='admin'))

        assert permissions.get_caller_sub(Request(raw)) is None

    def test_anonymous_caller_has_no_sub(self):
        """An unauthenticated request has no sub."""
        raw = APIRequestFactory().get('/')
        raw.user = AnonymousUser()

        assert permissions.get_caller_sub(Request(raw)) is None


class TestDefaultModelPermission:
    """The default permission a view composes with a trainee permission."""

    def test_everything_is_allowed_without_rest_authentication(self):
        """With authentication off, as in development, anonymous callers pass."""
        assert permissions.DefaultModelPermission().has_permission(drf_request('get'), UNIT_VIEW)

    @pytest.mark.usefixtures('rest_auth_enabled')
    def test_needs_model_permissions_with_rest_authentication(self, trainee, organizer):
        """With authentication on, the role's model permissions decide."""
        permission = permissions.DefaultModelPermission()

        assert permission.has_permission(drf_request('get', organizer), UNIT_VIEW)
        assert not permission.has_permission(drf_request('get', trainee), UNIT_VIEW)
        assert not permission.has_permission(drf_request('get'), UNIT_VIEW)


class TestAllocationUnitOwnerPermission:
    """A trainee may act on the allocation unit they allocated, and only on that."""

    @pytest.fixture
    def own_unit(self, pool, trainee):
        """A unit the trainee allocated for themselves."""
        return SandboxAllocationUnit.objects.create(
            pool=pool, created_by=trainee, created_by_sub='trainee-sub'
        )

    def test_owner_may_use_the_self_service_methods(self, trainee, own_unit):
        """The trainee who allocated the unit passes both checks."""
        permission = permissions.AllocationUnitOwnerPermission()
        request = drf_request('get', trainee)

        assert permission.has_permission(request, UNIT_VIEW)
        assert permission.has_object_permission(request, UNIT_VIEW, own_unit)

    def test_other_trainee_is_refused(self, other_trainee, own_unit):
        """Another trainee does not own the unit."""
        request = drf_request('get', other_trainee)

        assert not permissions.AllocationUnitOwnerPermission().has_object_permission(
            request, UNIT_VIEW, own_unit
        )

    def test_same_sub_from_another_issuer_is_refused(self, own_unit):
        """A second identity provider issuing the same sub is a different user."""
        impostor = auth_models.User.objects.create(username='trainee-sub|https://other-issuer.test')
        request = drf_request('get', impostor)

        assert not permissions.AllocationUnitOwnerPermission().has_object_permission(
            request, UNIT_VIEW, own_unit
        )

    def test_other_methods_are_not_self_service(self, trainee):
        """A method the view does not list for trainees needs the default permission."""
        request = drf_request('delete', trainee)

        assert not permissions.AllocationUnitOwnerPermission().has_permission(request, UNIT_VIEW)

    def test_caller_without_the_trainee_role_is_refused(self):
        """A user without the trainee role gets no self-service access."""
        request = drf_request('get', make_user('no-role-sub'))

        assert not permissions.AllocationUnitOwnerPermission().has_permission(request, UNIT_VIEW)


class TestOwnCreatorSubQueryPermission:
    """A trainee may list allocation units by creator for themselves only."""

    def test_own_sub_is_allowed(self, trainee):
        """Listing one's own units passes."""
        request = drf_request('get', trainee, data={'created_by_sub': 'trainee-sub'})

        assert permissions.OwnCreatorSubQueryPermission().has_permission(request, UNIT_VIEW)

    @pytest.mark.parametrize('query', [{'created_by_sub': 'other-trainee-sub'}, {}])
    def test_other_or_missing_sub_is_refused(self, trainee, query):
        """Listing someone else's units, or everyone's, is refused."""
        request = drf_request('get', trainee, data=query)

        assert not permissions.OwnCreatorSubQueryPermission().has_permission(request, UNIT_VIEW)


class TestTrainingAccessTokenPermission:
    """A trainee request must carry a training access token."""

    VIEW = SimpleNamespace(trainee_self_service_methods={'POST'})

    def test_request_with_the_header_is_admitted(self, trainee):
        """The token is checked by the view; the permission only needs the header."""
        request = drf_request('post', trainee, headers={'X-Training-Access-Token': 'token'})

        assert permissions.TrainingAccessTokenPermission().has_permission(request, self.VIEW)

    def test_request_without_the_header_is_refused(self, trainee):
        """Without a token the trainee has no self-service access."""
        request = drf_request('post', trainee)

        assert not permissions.TrainingAccessTokenPermission().has_permission(request, self.VIEW)


def test_caller_has_role_reads_the_synced_groups(trainee, organizer):
    """Roles come from the Django groups, without asking User-and-group again."""
    assert permissions.caller_has_role(drf_request('get', trainee), AccessLevel.TRAINEE)
    assert not permissions.caller_has_role(drf_request('get', trainee), AccessLevel.ORGANIZER)
    assert permissions.caller_has_role(drf_request('get', organizer), AccessLevel.ORGANIZER)
