from typing import Union

import keystoneauth1.identity
import keystoneauth1.session
from glanceclient.v2 import client as glance_client
from crczp.cloud_commons import CrczpException, TopologyInstance
from netaddr import IPNetwork, IPSet, AddrFormatError, IPAddress
from neutronclient.v2_0 import client as neutron_client
from novaclient import client as nova_client

NOVA_CLIENT_VERSION = "2.10"
HEAT_CLIENT_VERSION = 1
KEYSTONE_CLIENT_VERSION = 3

NAMES_REGEX = r"^[a-z]([a-z0-9A-Z-])*$"


def get_session(
    auth_url: str, application_credential_id: str, application_credential_secret: str
) -> keystoneauth1.session.Session:
    """
    Gets keystoneauth session.

    Takes parameters as generated in script you get from:
    ostack/project/compute/access and security/api access (right up 'Download Openstack RC File v3')

    :param auth_url: keystone authorization version url (for us )
    :param application_credential_id: OpenStack Application Credential ID
    :param application_credential_secret: OpenStack Application Credential Secret

    :return: instance of class keystoneauth1.session.Session
    """

    auth = keystoneauth1.identity.v3.ApplicationCredential(
        auth_url=auth_url,
        application_credential_id=application_credential_id,
        application_credential_secret=application_credential_secret,
    )

    return keystoneauth1.session.Session(auth=auth)


def get_client(
    client_type: str, session: keystoneauth1.session.Session
) -> Union[neutron_client.Client, glance_client.Client, nova_client.Client]:
    """
    Gets specific OpenStack client

    :param client_type: type of client to get.
                - 'neutron'     for neutron client (operates over networks)
                - 'glance'      for glance client (operates over images)
                - 'nova'        for nova client (operates over Server e.g. virtual machines)
    :param session: keystoneauth session

    :return:    instance of class glanceclient.v2.client for client_type 'glance'
                instance of class neutronclient.v2_0.client for client_type 'neutron'
                instance of class novaclient.client for client_type 'nova'

    :raise: ValueError if given client does not exists.
    """
    if client_type == "neutron":
        client = neutron_client.Client(session=session)
    elif client_type == "glance":
        client = glance_client.Client(session=session)
    elif client_type == "nova":
        client = nova_client.Client(session=session, version=NOVA_CLIENT_VERSION)
    else:
        raise ValueError("Unknown client type: {}".format(client_type))

    return client


def validate_topology_instance_networks(topology_instance: TopologyInstance) -> None:
    """
    Validates that networks do not overlap and IP addresses belongs to network.

    Compared to topology definition validation, it also validates
    that networks do not overlap with management networks.

    :param topology_instance: TopologyInstance for which networks are validated.
    :return: None
    :raise: CrczpException if networks are not valid
    """
    try:
        all_net_ip_pool = IPSet()

        for network in topology_instance.get_networks():
            ip_network = IPNetwork(network.cidr)

            if ip_network in all_net_ip_pool:
                raise CrczpException("network collision: {0}".format(network.cidr))

            all_net_ip_pool.add(ip_network)

            for link in topology_instance.get_network_links(network):
                if link.ip:
                    ip = IPAddress(link.ip)

                    if ip not in ip_network:
                        msg = (
                            f"Hosts IP not in network range. Host: {link.node.name}, {link.ip},"
                            f" Network: {network.name, network.cidr}"
                        )
                        raise CrczpException(msg)

    # can be raise if ip or cidr is not in valid ip addr format
    except AddrFormatError as e:
        raise CrczpException(e) from e
