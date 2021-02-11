from ruamel.yaml import ScalarNode
from yamlize import YamlizingError


def rename_deprecated_attribute(node_items, old_attribute_name, new_attribute_name):
    key_scalars = {key.value: key for key, _ in node_items if isinstance(key, ScalarNode)}
    if old_attribute_name in key_scalars:
        if new_attribute_name in key_scalars:
            msg = f'Deprecated attribute "{old_attribute_name}" is mutually exclusive ' \
                  f'with the new attribute "{new_attribute_name}".'
            raise YamlizingError(msg)
        key_scalars[old_attribute_name].value = new_attribute_name
