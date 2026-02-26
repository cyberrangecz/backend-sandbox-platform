"""
Module for image naming utilities.
"""

import re
from re import Pattern

from crczp.topology_definition.models import BaseBox, TopologyDefinition


def image_name_replace(
    prefix: str, replacement: str, topology_definition: TopologyDefinition
) -> TopologyDefinition:
    """
    Replace image name prefix.
    """
    pattern = re.compile(f'^{prefix}')

    for host in topology_definition.hosts:
        _image_name_do_replace(pattern, replacement, host.base_box)

    for router in topology_definition.routers:
        _image_name_do_replace(pattern, replacement, router.base_box)

    return topology_definition


def image_name_strip(prefix: str, topology_definition: TopologyDefinition) -> TopologyDefinition:
    """
    Strip image name prefix.
    """
    return image_name_replace(prefix, '', topology_definition)


def _image_name_do_replace(pattern: Pattern[str], replacement: str, base_box: BaseBox) -> None:
    """
    Perform image name replacement.
    """
    image_name: str = base_box.image
    base_box.image = re.sub(pattern, replacement, image_name, count=1)
