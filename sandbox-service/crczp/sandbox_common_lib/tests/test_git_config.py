"""Tests for the Git server resolution from sandbox definition URLs."""

import pytest

from crczp.sandbox_common_lib import exceptions, git_config
from crczp.sandbox_common_lib.crczp_config import CrczpConfiguration
from crczp.sandbox_definition_app.lib.definition_providers import GitlabProvider

UNPARSEABLE_URLS = ['abc', '', 'https://gitlab.com', 'https:///project.git']


class TestGetRestServer:
    """Extraction of the Git server base from a repository URL."""

    @pytest.mark.parametrize(
        ('url', 'expected_server'),
        [
            ('https://gitlab.com/crczp/project.git', 'https://gitlab.com/'),
            ('https://git.example.org:8443/project.git', 'https://git.example.org:8443/'),
        ],
    )
    def test_server_is_taken_from_url(self, url, expected_server):
        """The scheme and host (with port) up to the first slash form the server."""
        assert git_config.get_rest_server(url) == expected_server

    @pytest.mark.parametrize('url', UNPARSEABLE_URLS)
    def test_unparseable_url_raises_git_error(self, url):
        """A URL without a server followed by a path raises GitError, not ValueError."""
        with pytest.raises(exceptions.GitError, match='Cannot determine the Git server'):
            git_config.get_rest_server(url)

    @pytest.mark.parametrize('url', UNPARSEABLE_URLS)
    def test_provider_rejects_unparseable_url_with_git_error(self, url):
        """Creating a provider for an unparseable URL raises GitError."""
        with pytest.raises(exceptions.GitError):
            GitlabProvider(url, CrczpConfiguration())
