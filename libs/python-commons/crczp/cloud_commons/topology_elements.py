from typing import Union
from enum import Enum

from crczp.topology_definition.models import BaseBox, Network, Host, Router

Node = Union['MAN', Host, Router]


class MAN:
    """
    Represents Management Access Node in the TI.
    """
    def __init__(self, name: str, flavor: str, image: str, user: str):
        self.name = name
        self.flavor = flavor
        self.base_box = BaseBox()
        self.base_box.image = image
        self.base_box.mgmt_user = user

    def to_dict(self) -> dict:
        """
        Return the MAN representation as a dictionary.
        """
        return {
            'name': self.name,
            'flavor': self.flavor,
            'base_box': {
                'image': self.base_box.image,
                'user': self.base_box.mgmt_user,
            }
        }

    def __repr__(self):
        return 'MAN({0})'.format(self.to_dict())


class SecurityGroups(Enum):
    """
    Enumerator for sandboxes OpenStack security groups
    """
    SANDBOX_ACCESS = 'sandbox-access-sg'
    SANDBOX_MAN = 'sandbox-man-sg'
    SANDBOX_INTERNAL = 'sandbox-internal-sg'
    SANDBOX_MAN_INT = 'sandbox-man-int-sg'


class Link:
    """
    Represents a connection between a virtual machine and a virtual network in the TI.
    """
    def __init__(self, name: str, node: Node, network: Network, security_group: SecurityGroups,
                 ip: str = None, mac: str = None):
        self.name = name
        self.node = node
        self.network = network
        self.security_group = security_group.value
        self.ip = ip
        self.mac = mac

    def to_dict(self) -> dict:
        """
        Return the Link representation as a dictionary.
        """
        ret = {
            'name': self.name,
            'node': self.node.name,
            'network': self.network.name,
        }
        if self.ip:
            ret['ip'] = self.ip
        if self.mac:
            ret['mac'] = self.mac
        return ret

    def __repr__(self):
        return 'Link({0})'.format(self.to_dict())


class NodeToNodeLinkPair:
    """
    Represents a connection between two virtual machines over one virtual network in the TI.
    """
    def __init__(self, first: Link, second: Link):
        if first.network != second.network:
            msg = 'both links of NodeToNodeLinkPair have to be connected to the same network.'
            raise ValueError('{0} given links: \'{1}\', \'{2}\''.format(msg, first, second))
        if first.node == second.node:
            msg = 'links of NodeToNodeLinkPair have to be connected to different nodes.'
            raise ValueError('{0} given links: \'{1}\', \'{2}\''.format(msg, first, second))
        self.first = first
        self.second = second

    def __repr__(self):
        first_ip = self.first.ip if self.first.ip else ''
        second_ip = self.second.ip if self.second.ip else ''
        return 'NodeToNodeLinkPair({0}({1}) <- {2} -> {3}({4}) <- {5} -> {6}({7}))' \
            .format(self.first.node.name, first_ip, self.first.name, self.first.network.name,
                    self.first.network.cidr, self.second.name, self.second.node.name, second_ip)
