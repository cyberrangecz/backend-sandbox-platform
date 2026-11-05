"""Utility functions for OpenStack clients."""

import keystoneauth1.identity
import keystoneauth1.session
from openstack import connection

# The compute microversion every Nova request is pinned to. A negotiated version would vary
# the response shapes with each cloud's maximum, and from 2.92 Nova stops generating a key
# for a keypair created without a public key.
COMPUTE_API_VERSION = '2.10'

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


def get_connection(session: keystoneauth1.session.Session) -> connection.Connection:
    """
    Wraps an existing keystoneauth session in an openstacksdk Connection.

    The connection reuses the session as it is and reads neither clouds.yaml nor OS_*
    environment variables. Building it does no network I/O: the token is fetched and each
    service discovered on its first use.

    :param session: keystoneauth session
    :return: instance of class openstack.connection.Connection
    """
    return connection.Connection(
        session=session,
        compute_api_version=COMPUTE_API_VERSION,
        # Only the v2 image API is used. This picks the v2 proxy at runtime; for ty,
        # `connection.image` stays v1 | v2, hence the cast in OpenStackProxy._image.
        image_api_version='2',
        # The SDK retries an image request 5 times on HTTP 503 by default. Fail at once,
        # like every other service call.
        image_status_code_retries=0,
    )
