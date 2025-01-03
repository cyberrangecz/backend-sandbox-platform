from typing import List
from yamlize import Attribute, Object, StrList


BASE_NETWORK = 'crczp-network'
MAN_OUT_PORT = 'man-out-port'

SANDBOX_MAN_CIDR = '192.168.128.0/17'  # 32766 addresses

DNS_NAME_SERVERS = tuple()


class TransformationConfiguration(Object):
    base_network = Attribute(type=str, default=BASE_NETWORK)
    man_out_port = Attribute(type=str, default=MAN_OUT_PORT)

    man_image = Attribute(type=str)
    man_flavor = Attribute(type=str)
    man_user = Attribute(type=str)

    sandbox_man_cidr = Attribute(type=str, default=SANDBOX_MAN_CIDR)

    dns_name_servers = Attribute(type=StrList, default=DNS_NAME_SERVERS)

    def __init__(self, man_image: str, man_flavor: str, man_user: str,
                 base_network: str = BASE_NETWORK, man_out_port: str = MAN_OUT_PORT,
                 sandbox_man_cidr: str = SANDBOX_MAN_CIDR, dns_name_servers: List[str] = None):
        self.man_image = man_image
        self.man_flavor = man_flavor
        self.man_user = man_user
        self.base_network = base_network
        self.man_out_port = man_out_port
        self.sandbox_man_cidr = sandbox_man_cidr
        if dns_name_servers is None:
            dns_name_servers = DNS_NAME_SERVERS
        self.dns_name_servers = dns_name_servers

    @staticmethod
    def from_file(file) -> 'TransformationConfiguration':
        return TransformationConfiguration.load(open(file, mode='r'))
