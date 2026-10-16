"""Utility functions for OpenStack clients."""

from typing import Literal, overload

import keystoneauth1.identity
import keystoneauth1.session
from glanceclient.v2 import client as glance_client
from neutronclient.v2_0 import client as neutron_client
from novaclient import client as nova_client
from novaclient.v2 import client as nova_v2_client

NOVA_CLIENT_VERSION = '2.10'
HEAT_CLIENT_VERSION = 1
KEYSTONE_CLIENT_VERSION = 3

NAMES_REGEX = r'^[a-z]([a-z0-9A-Z-])*$'


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


# `novaclient.client.Client` is a version-dispatching factory *function*, not a class, so the
# concrete class behind NOVA_CLIENT_VERSION ('2.x') has to be named explicitly in annotations.
OpenStackClient = neutron_client.Client | glance_client.Client | nova_v2_client.Client


@overload
def get_client(
    client_type: Literal['neutron'], session: keystoneauth1.session.Session
) -> neutron_client.Client: ...


@overload
def get_client(
    client_type: Literal['glance'], session: keystoneauth1.session.Session
) -> glance_client.Client: ...


@overload
def get_client(
    client_type: Literal['nova'], session: keystoneauth1.session.Session
) -> nova_v2_client.Client: ...


@overload
def get_client(client_type: str, session: keystoneauth1.session.Session) -> OpenStackClient: ...


def get_client(client_type: str, session: keystoneauth1.session.Session) -> OpenStackClient:
    """
    Gets specific OpenStack client

    :param client_type: type of client to get.
                - 'neutron'     for neutron client (operates over networks)
                - 'glance'      for glance client (operates over images)
                - 'nova'        for nova client (operates over Server e.g. virtual machines)
    :param session: keystoneauth session

    :return:    instance of class glanceclient.v2.client for client_type 'glance'
                instance of class neutronclient.v2_0.client for client_type 'neutron'
                instance of class novaclient.v2.client for client_type 'nova'

    :raise: ValueError if given client does not exists.
    """
    if client_type == 'neutron':
        client = neutron_client.Client(session=session)
    elif client_type == 'glance':
        client = glance_client.Client(session=session)
    elif client_type == 'nova':
        client = nova_client.Client(session=session, version=NOVA_CLIENT_VERSION)
    else:
        raise ValueError(f'Unknown client type: {client_type}')

    return client
