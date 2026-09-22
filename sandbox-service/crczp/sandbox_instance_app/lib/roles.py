"""Per-request resolution of a requesting user's roles for a pool."""

from rest_framework.request import Request

from crczp.cloud_commons import UNIVERSAL_ROLES, UsersRoles
from crczp.sandbox_instance_app.models import Pool, PoolRoleGrant
from crczp.sandbox_uag.permissions import AdminPermission, OrganizerPermission


def resolve_users_roles(request: Request, pool: Pool) -> UsersRoles:
    """Resolve the requesting user's role set for the given pool."""
    if OrganizerPermission().has_permission(request, None) or AdminPermission().has_permission(
        request, None
    ):
        return UNIVERSAL_ROLES

    uag_user_id = getattr(request.user, 'uag_user_id', None)
    if uag_user_id is None:
        return frozenset()

    return frozenset(
        PoolRoleGrant.objects.filter(pool=pool, user=uag_user_id).values_list('role', flat=True)
    )
