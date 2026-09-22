"""Authentication and role management utilities for sandbox UAG."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

import requests
import structlog
import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission, User
from django.core.cache import caches
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from crczp.sandbox_uag.oidc_jwt import JWTAccessTokenAuthentication

from . import exceptions

LOG = structlog.get_logger()
authenticator_class = JWTAccessTokenAuthentication()

USER_CACHE_TIMEOUT = 300
CACHE = caches['uag_auth_groups_cache']
UAG_SETTINGS = settings.SANDBOX_UAG
# Bumped whenever the cache value's shape changes, so a stale entry from a previous
# shape is never misread as the current one.
CACHE_KEY_VERSION = 'v2'


@dataclass(frozen=True)
class UagIdentity:
    """The caller's identity as resolved from the user-and-group service."""

    user_id: int
    role_names: list[str]


def get_or_create_user(request: Request, user_info: dict[str, Any]) -> User:  # pylint: disable=too-many-locals
    """
    Retrieve (or create if non-existent) user from database.
    Set corresponding roles (Django groups).

    :return: User instance
    """
    # noinspection PyPep8Naming
    bearer_token = authenticator_class.get_bearer_token(request)

    sub = cast(str, user_info.get('sub', ''))
    iss = authenticator_class.extract_issuer(bearer_token)

    # username should ALWAYS uniquely identify the user, which is possible only with
    # {sub, iss} pair.
    # the limit for username is 140 characters so this may be a problem for long sub+iss
    username = get_unique_username(sub, iss)

    # this is suggested way of getting User model; the project does not swap
    # AUTH_USER_MODEL, so it is always django.contrib.auth.models.User
    user_cls = cast(type[User], get_user_model())
    (user, _) = user_cls.objects.update_or_create(
        username=username,
        defaults=cast(
            dict[str, Any],
            {
                'first_name': user_info.get('given_name'),
                'last_name': user_info.get('family_name'),
                'email': user_info.get('email'),
            },
        ),
    )

    cache_key = get_cache_key(username, bearer_token)

    cached_value = CACHE.get(cache_key)
    if cached_value is not None:
        (cached_groups, cached_bearer_token, cached_uag_user_id) = cached_value
        # if there is a key collision due to SHA-1 clash, this will detect it
        if cached_bearer_token == bearer_token:
            user.groups.set(cached_groups)
            user.uag_user_id = cached_uag_user_id  # ty: ignore[unresolved-attribute]
            return user

    try:
        identity = get_uag_identity(UAG_SETTINGS['ROLES_ACQUISITION_URL'], bearer_token)
    except Exception as ex:
        raise AuthenticationFailed(str(ex)) from ex

    LOG.debug('roles:', user_roles=identity.role_names)
    groups = []
    for group_name in identity.role_names:
        # We work only with existing predefined groups.
        try:
            group = Group.objects.get(name=group_name)
        except Group.DoesNotExist as ex:
            LOG.warning(
                'role not in database',
                username=user.username,
                sub=sub,
                iss=iss,
                role=group_name,
                exc_info=ex,
            )
            raise AuthenticationFailed('Authentication failed.') from ex
        groups.append(group)

    user.groups.set(groups)
    # Dynamic attribute: User declares no uag_user_id, but every instance carries a
    # __dict__, so the assignment is valid and callers may read it back for the request.
    user.uag_user_id = identity.user_id  # ty: ignore[unresolved-attribute]
    value_to_cache = (groups, bearer_token, identity.user_id)
    CACHE.set(cache_key, value_to_cache, USER_CACHE_TIMEOUT)

    return user


def get_cache_key(username: str, bearer_token: bytes) -> str:
    """Build a cache key from username and a hashed bearer token."""
    # Cache key shouldn't be longer than 250 characters, so we need to shorten the bearer token.
    # Since there is a possibility of a hash collision, additional steps are needed after cache-hit.
    # In our case, we check if the bearer tokens actually match.
    hashed_bearer_token = hashlib.sha1(  # nosec B324
        str(bearer_token).encode('UTF-8'), usedforsecurity=False
    ).hexdigest()  # nosec B324
    # 140 + 40 + 1 < 250
    return f'{CACHE_KEY_VERSION}|{username}|{hashed_bearer_token}'


def get_unique_username(sub: str, iss: str) -> str:
    """Return a unique username string derived from OIDC subject and issuer."""
    return f'{str(sub)}|{str(iss)}'


def get_uag_identity(url: str, bearer_token: bytes) -> UagIdentity:
    """Get the caller's user-and-group id and roles for this microservice."""
    err_msg = f"Failed to get User roles from '{url}': "

    headers = {'Authorization': f'Bearer {bearer_token.decode("ascii")}'}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

    # All request exceptions inherit from requests.RequestException
    except (ConnectionError, requests.RequestException) as ex:
        raise exceptions.NetworkError(err_msg + str(ex)) from ex

    try:
        response_json = response.json()
        role_names = [
            role['role_type']
            for role in response_json['roles']
            if role['name_of_microservice'] == UAG_SETTINGS['MICROSERVICE_NAME']
        ]
        user_id = response_json['id']
    except ValueError as ex:
        raise ValueError(err_msg + 'Invalid JSON returned.') from ex

    return UagIdentity(user_id=user_id, role_names=role_names)


def post_user_roles(
    url: str = UAG_SETTINGS['ROLES_REGISTRATION_URL'],
    roles_path: str = UAG_SETTINGS['ROLES_DEFINITION_PATH'],
) -> None:
    """Register roles (Django groups) to User-and-group service."""
    err_msg = f"Failed to post User roles to '{url}': "

    roles = create_roles(roles_path)

    data = json.dumps(create_roles_post_data(roles), indent=2)
    headers = {'Content-Type': 'application/json'}
    LOG.debug('Posting roles', data=data, headers=headers, url=url)
    try:
        response = requests.post(url, data=data, headers=headers, timeout=30)
        response.raise_for_status()

    # All request exceptions inherit from requests.RequestException
    except (ConnectionError, requests.RequestException) as ex:
        raise exceptions.NetworkError(err_msg + str(ex)) from ex

    LOG.info('Roles successfully posted', url=url)


def create_roles_post_data(roles: list[dict[str, Any]]) -> dict[str, Any]:
    """Create body for POST to User-and-group service."""
    return {
        'endpoint': UAG_SETTINGS['ENDPOINT'],
        'name': UAG_SETTINGS['MICROSERVICE_NAME'],
        'roles': [
            {'role_type': get_full_role_name(role['name']), 'default': role['default']}
            for role in roles
        ],
    }


def create_roles(roles_path: str) -> list[dict[str, Any]]:
    """Create roles (Django groups) and set permissions to them.
    Permissions MUST EXIST! Unlike groups which are created if do not exist.
    """
    with open(roles_path, encoding='utf-8') as f:
        roles = yaml.full_load(f)
    for role in roles:
        (group, _) = Group.objects.get_or_create(name=get_full_role_name(role['name']))
        permissions = [Permission.objects.get(codename=name) for name in role['permissions']]
        group.permissions.set(permissions)
    return cast(list[dict[str, Any]], roles)


def get_full_role_name(
    name: str,
    role_prefix: str = UAG_SETTINGS['ROLE_PREFIX'],
    microservice_name: str = UAG_SETTINGS['MICROSERVICE_NAME'].upper(),
) -> str:
    """Return fully qualified role name."""
    return f'{role_prefix.upper()}_{microservice_name.upper()}_{name.upper()}'
