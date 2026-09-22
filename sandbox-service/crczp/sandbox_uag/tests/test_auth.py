"""Tests for user-and-group identity resolution and cache-key namespacing."""

import hashlib

from crczp.sandbox_uag import auth


class TestGetUagIdentity:
    """Tests for get_uag_identity."""

    def test_returns_id_and_roles_for_this_microservice(self, mocker):
        """The caller's user id and this microservice's roles come from one response."""
        mock_response = mocker.MagicMock()
        mock_response.json.return_value = {
            'id': 42,
            'roles': [
                {
                    'role_type': 'ROLE_SANDBOX-SERVICE_ORGANIZER',
                    'name_of_microservice': auth.UAG_SETTINGS['MICROSERVICE_NAME'],
                },
                {
                    'role_type': 'ROLE_OTHER-SERVICE_ADMIN',
                    'name_of_microservice': 'some-other-service',
                },
            ],
        }
        mocker.patch('crczp.sandbox_uag.auth.requests.get', return_value=mock_response)

        identity = auth.get_uag_identity('http://uag.example/roles', b'token')

        assert identity.user_id == 42
        assert identity.role_names == ['ROLE_SANDBOX-SERVICE_ORGANIZER']

    def test_no_additional_call_beyond_the_one_response(self, mocker):
        """Resolving the identity costs exactly the one HTTP call already made for roles."""
        mock_get = mocker.patch('crczp.sandbox_uag.auth.requests.get')
        mock_get.return_value.json.return_value = {'id': 1, 'roles': []}

        auth.get_uag_identity('http://uag.example/roles', b'token')

        mock_get.assert_called_once()


class TestCacheKeyNamespacing:
    """Tests for the auth cache key's version namespace."""

    def test_cache_key_carries_the_current_version(self):
        """Every cache key is prefixed with the current schema version."""
        key = auth.get_cache_key('sub|iss', b'token')
        assert key.startswith(f'{auth.CACHE_KEY_VERSION}|')

    def test_a_pre_version_key_never_collides_with_the_current_one(self):
        """A key computed the way it was before the identity was carried (no version
        prefix) never matches the current key, so a stale cache entry is never read as
        though it carried the new shape."""
        username = 'sub|iss'
        bearer_token = b'token'
        hashed_bearer_token = hashlib.sha1(  # nosec B324
            str(bearer_token).encode('UTF-8'), usedforsecurity=False
        ).hexdigest()
        pre_version_key = f'{username}|{hashed_bearer_token}'

        assert auth.get_cache_key(username, bearer_token) != pre_version_key
