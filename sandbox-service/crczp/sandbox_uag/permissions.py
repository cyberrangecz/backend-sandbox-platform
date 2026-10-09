"""Permission classes for sandbox REST API endpoint access control."""

from collections.abc import Mapping
from enum import Enum
from typing import Any, override

from django.conf import settings
from django.contrib.auth.models import Group
from rest_framework import permissions
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request

from crczp.sandbox_common_lib.permissions import ModelPermissions
from crczp.sandbox_uag.auth import get_user_roles
from crczp.sandbox_uag.oidc_jwt import JWTAccessTokenAuthentication

authenticator_class = JWTAccessTokenAuthentication()
UAG_SETTINGS = settings.SANDBOX_UAG


class EndpointPermissionClass(permissions.BasePermission):
    """Base permission class that checks user roles against required access levels."""

    class AccessLevel(Enum):
        """Enumeration of available access levels for sandbox endpoints."""

        TRAINEE = 1
        DESIGNER = 2
        ORGANIZER = 3
        ADMIN = 4

    @staticmethod
    def get_role_string(level: 'EndpointPermissionClass.AccessLevel') -> str:
        """Return the full role string for a given access level."""
        return f'ROLE_SANDBOX-SERVICE_{level.name}'

    @staticmethod
    def has_access_level(request: Request, level: 'EndpointPermissionClass.AccessLevel') -> bool:
        """Check whether the request bearer token grants the required access level."""
        if not settings.CRCZP_SERVICE_CONFIG.authentication.authenticated_rest_api:
            return True

        bearer_token = authenticator_class.get_bearer_token(request)
        users_roles_names = get_user_roles(UAG_SETTINGS['ROLES_ACQUISITION_URL'], bearer_token)
        role_name = EndpointPermissionClass.get_role_string(level)
        return role_name in users_roles_names


class TraineePermission(EndpointPermissionClass):
    """Permission class granting access to users with the TRAINEE role."""

    @override
    def has_permission(self, request: Request, view: Any) -> bool:
        """Return True if the request user has at least TRAINEE access level."""
        return self.has_access_level(request, self.AccessLevel.TRAINEE)


class DesignerPermission(EndpointPermissionClass):
    """Permission class granting access to users with the DESIGNER role."""

    @override
    def has_permission(self, request: Request, view: Any) -> bool:
        """Return True if the request user has at least DESIGNER access level."""
        return self.has_access_level(request, self.AccessLevel.DESIGNER)


class OrganizerPermission(EndpointPermissionClass):
    """Permission class granting access to users with the ORGANIZER role."""

    @override
    def has_permission(self, request: Request, view: Any) -> bool:
        """Return True if the request user has at least ORGANIZER access level."""
        return self.has_access_level(request, self.AccessLevel.ORGANIZER)


class AdminPermission(EndpointPermissionClass):
    """Permission class granting access to users with the ADMIN role."""

    @override
    def has_permission(self, request: Request, view: Any) -> bool:
        """Return True if the request user has at least ADMIN access level."""
        return self.has_access_level(request, self.AccessLevel.ADMIN)


# Trainee self-service: a trainee may act on the allocation unit they allocated for
# themselves, and on nothing else. Views combine one of the classes below with
# DefaultModelPermission, so organizers and admins keep their model permissions.

TRAINING_ACCESS_TOKEN_HEADER = 'X-Training-Access-Token'  # noqa: S105 - a header name, not a secret


def is_rest_auth_enabled() -> bool:
    """Whether REST API requests must be authenticated."""
    return bool(settings.CRCZP_SERVICE_CONFIG.authentication.authenticated_rest_api)


def get_caller_sub(request: Request) -> str | None:
    """Return the OIDC sub of the authenticated caller, or None for an anonymous one.

    The sub comes from the userinfo the authentication verified the bearer token with,
    never from the request body or from parsing the token again. Usernames are
    '<sub>|<issuer>' (auth.get_unique_username) and serve as the fallback; a sub may
    contain '|', an issuer URL may not.
    """
    user = request.user
    if not user or not user.is_authenticated:
        return None
    auth = request.auth
    if isinstance(auth, Mapping):
        sub = auth.get('sub')
        if isinstance(sub, str) and sub:
            return sub
    sub, separator, _issuer = user.get_username().rpartition('|')
    return sub if separator and sub else None


def caller_has_role(request: Request, level: EndpointPermissionClass.AccessLevel) -> bool:
    """Check the caller's role in the Django groups the authentication synced from UAG.

    Unlike EndpointPermissionClass.has_access_level, this makes no User-and-group call, so
    it suits endpoints that the training service polls.
    """
    user = request.user
    if not user or not user.is_authenticated:
        return False
    return Group.objects.filter(
        user=user, name=EndpointPermissionClass.get_role_string(level)
    ).exists()


class DefaultModelPermission(permissions.BasePermission):
    """The project's default permission, for views that combine it with another one.

    settings.py makes ModelPermissions with IsAuthenticated the default only when REST
    authentication is on, and allows everything otherwise. Listing permission_classes on a
    view replaces that default, so such a view composes this class instead.
    """

    @override
    def has_permission(self, request: Request, view: Any) -> bool:
        """Apply the default permission classes for the current authentication setting."""
        if not is_rest_auth_enabled():
            return True
        authenticated = IsAuthenticated().has_permission(request, view)
        return authenticated and ModelPermissions().has_permission(request, view)


class TraineeSelfServicePermission(permissions.BasePermission):
    """Base for permissions that let a trainee act only on what is their own.

    A view lists the HTTP methods trainees may use in trainee_self_service_methods; any
    other method needs the default permission. Subclasses add the ownership check. Without
    an authenticated caller this grants nothing.
    """

    @override
    def has_permission(self, request: Request, view: Any) -> bool:
        """Admit an authenticated trainee using one of the view's self-service methods."""
        return (
            request.method in getattr(view, 'trainee_self_service_methods', frozenset())
            and get_caller_sub(request) is not None
            and caller_has_role(request, EndpointPermissionClass.AccessLevel.TRAINEE)
        )


class AllocationUnitOwnerPermission(TraineeSelfServicePermission):
    """Lets a trainee act on an allocation unit they allocated for themselves."""

    @override
    def has_object_permission(self, request: Request, view: Any, obj: Any) -> bool:
        """Admit the trainee whose sub and user account created the unit.

        Checking the user as well keeps two identity providers that issue the same sub apart.
        """
        sub = get_caller_sub(request)
        return sub is not None and obj.created_by_sub == sub and obj.created_by == request.user


class OwnCreatorSubQueryPermission(TraineeSelfServicePermission):
    """Lets a trainee list allocation units by creator only for their own sub."""

    @override
    def has_permission(self, request: Request, view: Any) -> bool:
        """Admit a trainee whose created_by_sub query parameter is their own sub."""
        if not super().has_permission(request, view):
            return False
        return request.query_params.get('created_by_sub') == get_caller_sub(request)


class TrainingAccessTokenPermission(TraineeSelfServicePermission):
    """Lets a trainee send a request that carries a training access token.

    Only the header's presence is checked here; the view validates the token against the
    pool's lock, which it loads anyway.
    """

    @override
    def has_permission(self, request: Request, view: Any) -> bool:
        """Admit a trainee whose request carries the training access token header."""
        return super().has_permission(request, view) and (
            TRAINING_ACCESS_TOKEN_HEADER in request.headers
        )
