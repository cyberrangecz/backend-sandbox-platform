"""Transformation configuration module for cloud resource settings."""

from typing import Any

from pydantic import Field

from crczp.topology_definition.models import StrList
from crczp.topology_definition.yaml_model import YamlModel, YamlStr, positional_init

BASE_NETWORK = 'crczp-network'
MAN_OUT_PORT = 'man-out-port'

SANDBOX_MAN_CIDR = '192.168.128.0/17'  # 32766 addresses

DNS_NAME_SERVERS: tuple[str, ...] = ()


class TransformationConfiguration(YamlModel):
    """Configuration for cloud transformation settings."""

    base_network: YamlStr = BASE_NETWORK
    man_out_port: YamlStr = MAN_OUT_PORT

    man_image: YamlStr
    man_flavor: YamlStr
    man_user: YamlStr

    sandbox_man_cidr: YamlStr = SANDBOX_MAN_CIDR

    dns_name_servers: StrList = Field(default_factory=lambda: list(DNS_NAME_SERVERS))

    @positional_init
    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        man_image: str,
        man_flavor: str,
        man_user: str,
        base_network: str = BASE_NETWORK,
        man_out_port: str = MAN_OUT_PORT,
        sandbox_man_cidr: str = SANDBOX_MAN_CIDR,
        dns_name_servers: list[str] | None = None,
        **data: Any,
    ) -> None:
        fields: dict[str, Any] = {
            'man_image': man_image,
            'man_flavor': man_flavor,
            'man_user': man_user,
            'base_network': base_network,
            'man_out_port': man_out_port,
            'sandbox_man_cidr': sandbox_man_cidr,
            'dns_name_servers': (
                list(DNS_NAME_SERVERS) if dns_name_servers is None else dns_name_servers
            ),
            **data,
        }
        super().__init__(**fields)
