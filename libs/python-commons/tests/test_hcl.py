"""Tests for crczp.cloud_commons.hcl."""

import pytest

from crczp.cloud_commons import hcl_string


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        pytest.param('debian-12', '"debian-12"', id='plain-value-only-quoted'),
        pytest.param(
            'x"\n}\nresource "a" "b" {}',
            '"x\\"\\n}\\nresource \\"a\\" \\"b\\" {}"',
            id='block-break-out',
        ),
        pytest.param('${file("p")}', '"$${file(\\"p\\")}"', id='interpolation'),
        pytest.param(
            '%{ if true }x%{ endif }',
            '"%%{ if true }x%%{ endif }"',
            id='template-directive',
        ),
        pytest.param('a\\', '"a\\\\"', id='trailing-backslash'),
        pytest.param('\x08', '"\\u0008"', id='control-character'),
        pytest.param('a\tb\rc', '"a\\tb\\rc"', id='tab-and-carriage-return'),
        pytest.param('$${x}', '"$$${x}"', id='already-doubled-dollar'),
        pytest.param('ž\\${', '"ž\\\\$${"', id='backslash-before-interpolation'),
        pytest.param('', '""', id='empty'),
    ],
)
def test_hcl_string(value: str, expected: str) -> None:
    """Free text is rendered as a single HCL string literal that evaluates back to the input."""
    assert hcl_string(value) == expected


def test_hcl_string_stringifies_non_strings() -> None:
    """Template values may arrive as numbers; they render as their string form."""
    assert hcl_string(42) == '"42"'
