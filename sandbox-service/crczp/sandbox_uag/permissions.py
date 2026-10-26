"""Permission classes for sandbox REST API endpoint access control."""

from enum import Enum
from typing import Any, cast, override

from django.conf import settings
from django.contrib.auth.models import User
from rest_framework import permissions
from rest_framework.request import Request


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
        """Check whether the requesting user's resolved groups grant the required access level.

        Reads the groups OIDC authentication already resolved onto ``request.user``
        (``auth.get_or_create_user``); makes no User-and-group call of its own.
        """
        if not settings.CRCZP_SERVICE_CONFIG.authentication.authenticated_rest_api:
            return True

        role_name = EndpointPermissionClass.get_role_string(level)
        # the project does not swap AUTH_USER_MODEL, so an authenticated request.user
        # is always django.contrib.auth.models.User
        return cast(User, request.user).groups.filter(name=role_name).exists()


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
