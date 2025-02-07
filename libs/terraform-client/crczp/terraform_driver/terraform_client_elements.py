from enum import Enum
from typing import Union, Dict

from crczp.cloud_commons.cloud_client_elements import Image


class CrczpTerraformBackendType(Enum):
    LOCAL = 'local'
    POSTGRES = 'pg'
    KUBERNETES = 'kubernetes'


class TerraformInstance:
    """
    Used to represent terraform stack instance
    """

    def __init__(self, name: str, instance_id: str, status: str, image: Image, flavor_name: str):
        self.name = name
        self.id = instance_id
        self.status = status
        if self.status is None:
            self.status = "UNKNOWN"
        self.image = image
        self.flavor_name = flavor_name
        self.links = {}

    def add_link(self, network: str, ip: Dict[str, Union[str, int]]) -> None:
        self.links[network] = ip

    def __repr__(self):
        return "<TerraformStackInstance\n" \
               "  name: {0.name},\n" \
               "  id: {0.id},\n" \
               "  status: {0.status},\n" \
               "  image: {0.image},\n" \
               "  flavor_name: {0.flavor_name},\n" \
               "  links: {0.links}>\n".format(self)
