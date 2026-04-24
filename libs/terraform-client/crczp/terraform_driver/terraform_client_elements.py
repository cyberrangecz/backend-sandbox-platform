from enum import Enum
from typing import Union

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
            self.status = 'UNKNOWN'
        self.image = image
        self.flavor_name = flavor_name
        self.links: dict[str, dict[str, Union[str, int]]] = {}

    def add_link(self, network: str, ip: dict[str, Union[str, int]]) -> None:
        self.links[network] = ip

    def __repr__(self) -> str:  # type: ignore[explicit-override]
        return (
            '<TerraformStackInstance\n'
            f'  name: {self.name},\n'
            f'  id: {self.id},\n'
            f'  status: {self.status},\n'
            f'  image: {self.image},\n'
            f'  flavor_name: {self.flavor_name},\n'
            f'  links: {self.links}>\n'
        )
