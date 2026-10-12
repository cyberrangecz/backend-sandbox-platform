"""
Module for topology definition utilities.
"""

from typing import Any

from ruamel.yaml.nodes import ScalarNode
from yamlize import YamlizingError


def rename_deprecated_attribute(
    node_items: Any,
    old_attribute_name: str,
    new_attribute_name: str,
) -> None:
    """
    Rename deprecated attribute in YAML node.
    """
    key_scalars = {key.value: key for key, _ in node_items if isinstance(key, ScalarNode)}

    if old_attribute_name in key_scalars:
        if new_attribute_name in key_scalars:
            msg = (
                f'Deprecated attribute "{old_attribute_name}" is mutually exclusive '
                f'with the new attribute "{new_attribute_name}".'
            )
            raise YamlizingError(msg)

        key_scalars[old_attribute_name].value = new_attribute_name


def reject_key_conflict(
    node_items: Any,
    key_a: str,
    key_b: str,
) -> None:
    """
    Raise if a YAML mapping node declares both of two mutually exclusive keys.
    """
    key_scalars = {key.value for key, _ in node_items if isinstance(key, ScalarNode)}

    if key_a in key_scalars and key_b in key_scalars:
        msg = f'Attribute "{key_a}" is mutually exclusive with attribute "{key_b}".'
        raise YamlizingError(msg)


def reject_null_value(
    node_items: Any,
    key: str,
) -> None:
    """
    Raise if a YAML mapping node declares the given key with an explicit null value.
    """
    for key_node, value_node in node_items:
        if (
            isinstance(key_node, ScalarNode)
            and key_node.value == key
            and isinstance(value_node, ScalarNode)
            and value_node.tag == 'tag:yaml.org,2002:null'
        ):
            raise YamlizingError(f'Attribute "{key}" must not be null.')
