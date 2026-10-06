"""Tests for CRCZP configuration parsing."""

import os
from io import StringIO
from pathlib import Path

import pytest

from crczp.sandbox_common_lib.crczp_config import TopologyCacheMode
from crczp.sandbox_common_lib.crczp_service_config import CrczpServiceConfig
from crczp.sandbox_common_lib.exceptions import ImproperlyConfigured

TEST_CONFIG = (
    Path(__file__).parents[2] / 'sandbox_service_project' / 'tests' / 'config.yml'
).read_text(encoding='utf-8')
APP_CONFIG_KEY = 'application_configuration:\n'
CONSOLE_TYPE_LINE = '    #os_console_type: spice-html5\n'
IDENTITY_FILE_LINE = '        IdentityFile: /tmp/id_rsa\n'


def _replace_once(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


def _with_app_setting(line: str, text: str = TEST_CONFIG) -> str:
    return _replace_once(text, APP_CONFIG_KEY, f'{APP_CONFIG_KEY}    {line}\n')


def _load(text: str) -> CrczpServiceConfig:
    return CrczpServiceConfig.load(StringIO(text))


class TestTopologyCacheModeCreate:
    """Tests for TopologyCacheMode.create input parsing."""

    @pytest.mark.parametrize(
        ('value', 'expected'),
        [
            ('AGGRESSIVE', TopologyCacheMode.AGGRESSIVE),
            ('FRESH', TopologyCacheMode.FRESH),
            ('FRESH_IMPORT', TopologyCacheMode.FRESH_IMPORT),
            ('fresh_import', TopologyCacheMode.FRESH_IMPORT),
            ('fresh-import', TopologyCacheMode.FRESH_IMPORT),
        ],
    )
    def test_create_valid(self, value, expected):
        """Test that valid values (case- and dash-insensitive) resolve to the right member."""
        assert TopologyCacheMode.create(value) is expected

    def test_create_invalid_raises_readable_value_error(self):
        """Test that an unknown value raises ValueError naming the value and valid options."""
        with pytest.raises(ValueError) as exc_info:
            TopologyCacheMode.create('BOGUS')
        message = str(exc_info.value)
        assert 'BOGUS' in message
        assert 'AGGRESSIVE' in message
        assert 'FRESH_IMPORT' in message


class TestServiceConfigLoad:
    """Tests loading variants of the real test config through CrczpServiceConfig.load."""

    @pytest.mark.parametrize('value', ['spice', ''])
    def test_invalid_console_type_lists_valid_values(self, value):
        """An unknown or empty os_console_type is a positioned ImproperlyConfigured."""
        text = _replace_once(TEST_CONFIG, CONSOLE_TYPE_LINE, f'    os_console_type: {value}\n')
        with pytest.raises(
            ImproperlyConfigured,
            match=r'(?s)Expected one of: novnc, xvpvnc, spice-html5, rdp-html5, serial, '
            r'webmks\..*start:',
        ):
            _load(text)

    @pytest.mark.parametrize('value', ['bogus', ''])
    def test_invalid_topology_cache_mode_lists_valid_values(self, value):
        """An unknown or empty topology_cache_mode is a positioned ImproperlyConfigured."""
        with pytest.raises(
            ImproperlyConfigured,
            match=r'(?s)Expected one of: AGGRESSIVE, FRESH, FRESH_IMPORT\..*start:',
        ):
            _load(_with_app_setting(f'topology_cache_mode: {value}'))

    @pytest.mark.parametrize(
        ('identity_file', 'expected'),
        [
            ('~/proxy-key', os.path.join(os.path.expanduser('~'), 'proxy-key')),
            ('keys/proxy-key', os.path.join(os.getcwd(), 'keys', 'proxy-key')),
        ],
    )
    def test_identity_file_made_absolute(self, identity_file, expected):
        """The ProxyJump IdentityFile is expanded and made absolute on load."""
        text = _replace_once(
            TEST_CONFIG, IDENTITY_FILE_LINE, f'        IdentityFile: {identity_file}\n'
        )
        assert _load(text).app_config.proxy_jump_to_man.IdentityFile == expected

    def test_empty_identity_file_stays_empty(self):
        """An unset IdentityFile is not turned into the working directory."""
        text = _replace_once(TEST_CONFIG, IDENTITY_FILE_LINE, '        IdentityFile: ""\n')
        assert _load(text).app_config.proxy_jump_to_man.IdentityFile == ''
