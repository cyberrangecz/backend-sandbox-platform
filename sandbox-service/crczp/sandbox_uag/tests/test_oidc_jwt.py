"""Tests for decoding OIDC bearer tokens and caching their userinfo."""

# pylint: disable=redefined-outer-name
import base64
import hashlib
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


def cache_key(token: bytes) -> str:
    """The userinfo cache key of a token."""
    return 'oidc-userinfo-' + hashlib.sha256(token).hexdigest()


@pytest.fixture
def userinfo_cache(mocker):
    """Replace the userinfo cache with a mock backed by a dict."""
    store: dict[str, object] = {}
    cache = mocker.patch.object(oidc_jwt, 'CACHE')
    cache.get.side_effect = store.get
    cache.set.side_effect = lambda key, value, _timeout: store.__setitem__(key, value)
    return cache


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


def test_get_userinfo_fetches_and_caches_on_miss(userinfo_cache, mocker):
    """On a cache miss the userinfo is fetched and cached under the token's hash."""
    fetch = mocker.patch.object(
        JWTAccessTokenAuthentication, '_get_userinfo', return_value={'sub': 'user-1'}
    )
    token = make_token({'iss': ISSUER, 'sub': 'user-1'})

    userinfo = JWTAccessTokenAuthentication().get_userinfo(token)

    assert userinfo == {'sub': 'user-1'}
    fetch.assert_called_once_with(token)
    userinfo_cache.set.assert_called_once_with(
        cache_key(token),
        {'sub': 'user-1'},
        oidc_jwt.oidc_auth_settings.OIDC_BEARER_TOKEN_EXPIRATION_TIME,
    )


@pytest.mark.usefixtures('userinfo_cache')
def test_get_userinfo_answers_the_same_token_from_cache(mocker):
    """Presenting an accepted token again does not ask the provider again."""
    fetch = mocker.patch.object(
        JWTAccessTokenAuthentication, '_get_userinfo', return_value={'sub': 'user-1'}
    )
    token = make_token({'iss': ISSUER, 'sub': 'user-1'})
    authenticator = JWTAccessTokenAuthentication()

    authenticator.get_userinfo(token)
    userinfo = authenticator.get_userinfo(token)

    assert userinfo == {'sub': 'user-1'}
    fetch.assert_called_once_with(token)


@pytest.mark.usefixtures('userinfo_cache')
def test_get_userinfo_does_not_answer_a_forged_token_from_cache(mocker):
    """A forged token claiming a cached user's `sub` is still checked by the provider."""
    genuine = make_token({'iss': ISSUER, 'sub': 'user-1'})
    forged = genuine.rsplit(b'.', 1)[0] + b'.forged-signature'
    fetch = mocker.patch.object(
        JWTAccessTokenAuthentication,
        '_get_userinfo',
        side_effect=[{'sub': 'user-1'}, AuthenticationFailed('Token rejected by the provider.')],
    )
    authenticator = JWTAccessTokenAuthentication()
    authenticator.get_userinfo(genuine)

    with pytest.raises(AuthenticationFailed):
        authenticator.get_userinfo(forged)

    assert fetch.call_count == 2
