"""
Helpers for rendering values into generated Terraform (HCL) templates.
"""

_HCL_ESCAPES = {'\\': '\\\\', '"': '\\"', '\n': '\\n', '\r': '\\r', '\t': '\\t'}


def hcl_string(value: object) -> str:
    """
    Return value as a quoted HCL string literal that evaluates to value.
    """
    escaped = ''.join(
        _HCL_ESCAPES.get(char, char if char >= ' ' else f'\\u{ord(char):04x}')
        for char in str(value)
    )
    # Inside a quoted HCL string, ${...} and %{...} are evaluated as template expressions.
    return '"' + escaped.replace('${', '$${').replace('%{', '%%{') + '"'
