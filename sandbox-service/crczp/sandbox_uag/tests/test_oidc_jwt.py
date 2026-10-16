"""Tests for decoding OIDC bearer tokens and caching their userinfo."""

# pylint: disable=redefined-outer-name
import base64
import json

import pytest
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from crczp.sandbox_uag import oidc_jwt
from crczp.sandbox_uag.oidc_jwt import JWTAccessTokenAuthentication

ISSUER = 'https://idp.example/realms/crczp'


def _segment(data: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')


def make_token(payload: object) -> bytes:
    """Build a JWT-shaped token. Nothing here checks the signature, so it is a placeholder."""
    return f'{_segment({"alg": "RS256", "typ": "JWT"})}.{_segment(payload)}.signature'.encode()


@pytest.fixture
def userinfo_cache(mocker):
    """Replace the userinfo cache with a mock."""
    return mocker.patch.object(oidc_jwt, 'CACHE')


@pytest.mark.parametrize('as_text', [False, True])
def test_extract_issuer_strips_trailing_slash(as_text):
    """The issuer is read from str and bytes tokens alike, without its trailing slash."""
    token = make_token({'iss': f'{ISSUER}/', 'sub': 'user-1'})

    issuer = JWTAccessTokenAuthentication.extract_issuer(token.decode() if as_text else token)

    assert issuer == ISSUER


def test_extract_issuer_without_issuer_claim_is_empty():
    """A token without `iss` yields no issuer, which no allowed provider matches."""
    assert JWTAccessTokenAuthentication.extract_issuer(make_token({'sub': 'user-1'})) == ''


@pytest.mark.parametrize(
    'token',
    [
        b'opaque-access-token',
        b'header.%%%.signature',
        b'header.' + base64.urlsafe_b64encode(b'not json') + b'.signature',
        make_token(['not', 'an', 'object']),
        make_token({'iss': 42}),
        b'h\xff.p.s',
    ],
    ids=['opaque', 'not-base64', 'not-json', 'not-an-object', 'non-string-issuer', 'not-ascii'],
)
def test_malformed_token_is_rejected(token):
    """Anything but a JWT with a JSON object payload fails authentication."""
    with pytest.raises(AuthenticationFailed):
        JWTAccessTokenAuthentication.extract_issuer(token)


def test_authenticate_rejects_opaque_token():
    """An opaque bearer token is a 401, not an unhandled error."""
    request = APIRequestFactory().get('/', HTTP_AUTHORIZATION='Bearer opaque-access-token')

    with pytest.raises(AuthenticationFailed):
        JWTAccessTokenAuthentication().authenticate(request)


def test_get_userinfo_returns_cached_entry(userinfo_cache, mocker):
    """A cached userinfo for the token's `sub` is returned without asking the provider."""
    userinfo_cache.get.return_value = {'sub': 'user-1'}
    fetch = mocker.patch.object(JWTAccessTokenAuthentication, '_get_userinfo')

    userinfo = JWTAccessTokenAuthentication().get_userinfo(make_token({'sub': 'user-1'}))

    assert userinfo == {'sub': 'user-1'}
    userinfo_cache.get.assert_called_once_with('oidc-sub-user-1')
    fetch.assert_not_called()


def test_get_userinfo_fetches_and_caches_on_miss(userinfo_cache, mocker):
    """On a cache miss the userinfo is fetched and cached under the token's `sub`."""
    userinfo_cache.get.return_value = None
    fetch = mocker.patch.object(
        JWTAccessTokenAuthentication, '_get_userinfo', return_value={'sub': 'user-1'}
    )
    token = make_token({'sub': 'user-1'})

    userinfo = JWTAccessTokenAuthentication().get_userinfo(token)

    assert userinfo == {'sub': 'user-1'}
    fetch.assert_called_once_with(token)
    userinfo_cache.set.assert_called_once_with(
        'oidc-sub-user-1',
        {'sub': 'user-1'},
        oidc_jwt.oidc_auth_settings.OIDC_BEARER_TOKEN_EXPIRATION_TIME,
    )


def test_get_userinfo_without_sub_is_not_cached(userinfo_cache, mocker):
    """Without a `sub` claim the userinfo is fetched every time and never cached."""
    mocker.patch.object(JWTAccessTokenAuthentication, '_get_userinfo', return_value={})

    JWTAccessTokenAuthentication().get_userinfo(make_token({'iss': ISSUER}))

    userinfo_cache.get.assert_not_called()
    userinfo_cache.set.assert_not_called()
