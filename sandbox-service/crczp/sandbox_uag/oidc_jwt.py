"""OIDC JWT authentication utilities for sandbox UAG."""

import base64
import hashlib
import json
from typing import Any, cast
from urllib.parse import urlparse

import requests
import structlog
from django.conf import settings
from django.core.cache import caches
from django.utils.translation import gettext as _
from oidc_auth.authentication import BearerTokenAuthentication
from oidc_auth.settings import api_settings as oidc_auth_settings
from oidc_auth.util import cache
from rest_framework.exceptions import AuthenticationFailed

LOG = structlog.get_logger()
WELL_KNOWN_CONFIG_CACHE_TTL = oidc_auth_settings.OIDC_BEARER_TOKEN_EXPIRATION_TIME
CACHE = caches['default']
OIDC_USERINFO_PREFIX = 'oidc-userinfo-'


def _decode_payload(token: str | bytes) -> dict[str, Any]:
    """
    Decode the payload of a JWT without verifying its signature.

    The claims only pick the OIDC provider; the token itself is verified by the
    provider's userinfo endpoint.

    :raises AuthenticationFailed: When the token is not a JWT with a JSON object payload.
    """
    try:
        text = token.decode('ascii') if isinstance(token, bytes) else token
        segment = text.split('.')[1]
        payload = json.loads(base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4)))
    except (IndexError, ValueError) as ex:
        raise AuthenticationFailed(_('Bearer token is not a valid JWT.')) from ex
    if not isinstance(payload, dict):
        raise AuthenticationFailed(_('Bearer token is not a valid JWT.'))
    return payload


class JWTAccessTokenAuthentication(BearerTokenAuthentication):
    """Use for Bearer token in JWT format. It allows multiple OIDC providers support."""

    @staticmethod
    def extract_issuer(token: str | bytes) -> str:
        """Extract the issuer from a JWT token payload."""
        issuer = _decode_payload(token).get('iss', '')
        if not isinstance(issuer, str):
            raise AuthenticationFailed(_('Bearer token is not a valid JWT.'))
        return issuer.rstrip('/')

    @cache(ttl=WELL_KNOWN_CONFIG_CACHE_TTL)
    def get_well_known_config(self, provider: dict[str, str]) -> dict[str, Any]:
        """Fetch and cache the OpenID Connect well-known configuration for the given provider."""
        well_known_config_url = provider.get('well_known_config')
        if not well_known_config_url:
            well_known_config_url = provider['issuer'] + '/.well-known/openid-configuration'
        response = requests.get(well_known_config_url, timeout=30)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def _get_userinfo(self, token: bytes) -> dict[str, Any]:
        issuer = self.extract_issuer(token)
        LOG.debug('Issuer extracted from token.', issuer=issuer)
        allowed_issuers = {
            provider['issuer']: provider
            for provider in settings.SANDBOX_UAG['ALLOWED_OIDC_PROVIDERS']
        }
        if issuer not in allowed_issuers:
            msg = _('Issuer of the token is not listed in ALLOWED_OIDC_PROVIDERS.')
            raise AuthenticationFailed(msg)

        url = urlparse(issuer)
        if url.scheme != 'https':
            LOG.warn('DANGER! OIDC issuer is not using https protocol.', issuer=issuer)

        provider = allowed_issuers[issuer]
        well_known_config = self.get_well_known_config(provider)
        userinfo_endpoint = provider.get('userinfo_endpoint')
        if not userinfo_endpoint:
            userinfo_endpoint = well_known_config['userinfo_endpoint']
        http_headers = {'Authorization': f'Bearer {token.decode("utf-8")}'}

        response = requests.get(userinfo_endpoint, headers=http_headers, timeout=30)
        response.raise_for_status()

        return cast(dict[str, Any], response.json())

    def get_userinfo(self, token: bytes) -> dict[str, Any]:
        # Keyed by the whole token, so the cache only answers for a token the provider
        # has already accepted. A key built from an unverified claim such as `sub` would
        # hand a cached user's info to any forged token carrying that claim.
        cache_key = OIDC_USERINFO_PREFIX + hashlib.sha256(token).hexdigest()
        userinfo = CACHE.get(cache_key)

        if not userinfo:
            userinfo = self._get_userinfo(token)
            CACHE.set(
                cache_key,
                userinfo,
                oidc_auth_settings.OIDC_BEARER_TOKEN_EXPIRATION_TIME,
            )
        return userinfo
