from typing import List

import novaclient.v2.keypairs
from crczp.cloud_commons import (
    CrczpCloudClientBase,
    Image,
    TransformationConfiguration,
    TopologyInstance,
    QuotaSet,
    HardwareUsage,
    Limits,
    NodeDetails,
)

from crczp.openstack_driver import utils, open_stack_proxy
from crczp.openstack_driver.decorators import check_authentication


class CrczpOpenStackClient(CrczpCloudClientBase):
    """
    client used as interface providing functions of this library

    takes parameters to authenticate to openstack.
    :raise: ValueError if some of used clients doesn't exist.
    """

    def __init__(
        self,
        auth_url: str,
        application_credential_id: str,
        application_credential_secret: str,
        trc: TransformationConfiguration,
    ):
        self.session = utils.get_session(auth_url, application_credential_id, application_credential_secret)
        self.glance_client = utils.get_client("glance", self.session)
        self.nova_client = utils.get_client("nova", self.session)
        self.neutron_client = utils.get_client("neutron", self.session)
        self.open_stack_proxy = open_stack_proxy.OpenStackProxy(
            self.glance_client,
            self.nova_client,
            self.neutron_client,
            auth_url,
            application_credential_id,
            application_credential_secret,
            trc,
        )

    @staticmethod
    def get_private_ip(instance_attrs: dict) -> str:
        """
        Get IP address of an instance.
        Note, AWS driver stores IP address in different format.

        :param instance_attrs: Terraoform instance attributes
        :return: IP address of an instance
        """
        return instance_attrs["all_fixed_ips"][0]

    @check_authentication
    def list_images(self) -> List[Image]:
        """
        Lists all images in openstack project.

        :return List of Image objects.
        """
        return self.open_stack_proxy.list_images()

    @check_authentication
    def get_terraform_provider(self):
        """
        Get OpenStack Terraform provider
        :return: Terraform provider template
        :raise InvalidTopologyDefinition: Terraform provider template is incorrect
        """
        return self.open_stack_proxy.get_terraform_provider()

    @check_authentication
    def create_terraform_template(self, topology_instance: TopologyInstance, *args, **kwargs) -> str:
        """
        validates topology definition.

        :param topology_instance: TopologyInstance
        :return HEAT Orchestration Template as a string
        :raise CrczpException if not valid
        """
        return self.open_stack_proxy.validate_and_get_terraform_template(topology_instance, *args, **kwargs)

    @check_authentication
    def get_image(self, image_id: str) -> Image:
        """
        Gets an Image object containing its details based on its id.

        :param image_id: id of the image to retrieve
        :return: Image
        """
        return self.open_stack_proxy.get_image(image_id)

    @check_authentication
    def resume_node(self, node_id: str) -> None:
        """
        Resume node.

        :param node_id: The ID of the node
        :return: None
        :raise CrczpException: Node not found
        """
        self.open_stack_proxy.resume_instance(node_id)

    @check_authentication
    def start_node(self, node_id: str) -> None:
        """
        Start node.

        :param node_id: The ID of the node
        :return: None
        :raise CrczpException: Node not found
        """
        self.open_stack_proxy.start_instance(node_id)

    @check_authentication
    def reboot_node(self, node_id: str) -> None:
        """
        Reboot node.

        :param node_id: The ID of the node
        :return: None
        :raise CrczpException: Node not found
        """
        self.open_stack_proxy.reboot_instance(node_id)

    def get_node_details(self, terraform_attrs: dict) -> NodeDetails:
        """
        Get node details from the Terraform resource attributes.
        Note, Terraform attributes are backend specific.

        :param terraform_attrs: Terraform resource attributes of the node
        :return: Node details
        """
        image_id = terraform_attrs["image_id"]
        status = terraform_attrs["power_state"]
        flavor = terraform_attrs["flavor_name"]

        return NodeDetails(image_id=image_id, status=status, flavor=flavor)

    @check_authentication
    def get_console_url(self, node_id: str, console_type: str) -> str:
        """
        Get console for given node.

        :param node_id: The ID of the node
        :param console_type: Type can be novnc, xvpvnc, spice-html5, rdp-html5, serial and webmks
        :return: Console url
        :raise CrczpException: Node not found
        """
        return self.open_stack_proxy.get_console_url(node_id, console_type)

    @check_authentication
    def create_keypair(self, name: str, public_key: str = None, key_type: str = "ssh") -> None:
        """
        Create key-pair in OpenStack. If public_key is not specified, new key-pair is created.

        :param name: Name of ssh key in stack.
        :param public_key: key to be stored, if None new is created
        :param key_type: type of public key from which keypair is created,
                         accepted values are 'ssh' and 'x509'
        :return: None
        :raise: CrczpException on creation failure
        """
        self.open_stack_proxy.create_keypair(name, public_key, key_type)

    @check_authentication
    def get_keypair(self, name: str) -> novaclient.v2.keypairs.Keypair:
        """
        Get keypair from nova

        :param name: Name of keypair to be returned
        :return: Keypair instance
        :raise: CrczpException if keypair does not exist
        """
        return self.open_stack_proxy.get_keypair(name)

    @check_authentication
    def delete_keypair(self, name: str) -> None:
        """
        Delete keypair in OpenStack.

        :param name: name of key to be deleted.
        :return: None
        :raise: CrczpException if keypair doesn't exist
        """
        self.open_stack_proxy.delete_keypair(name)

    @check_authentication
    def get_quota_set(self) -> QuotaSet:
        """
        Get quota set of OpenStack project.

        :return QuotaSet object
        """
        return self.open_stack_proxy.get_quota_set(self.session.get_project_id())

    @check_authentication
    def get_project_name(self) -> str:
        """
        Get project name from application credentials.

        :return The name of the OpenStack project
        """
        return self.session.auth.get_auth_ref(self.session).project_name

    @check_authentication
    def get_hardware_usage(self, topology_instance: TopologyInstance) -> HardwareUsage:
        """
        Gets Heat Stack hardware usage of a single sandbox.

        :param topology_instance: Topology instance of Heat Stack
        :return: Hardware usage a sandbox in a Heat Stack
        """
        return self.open_stack_proxy.get_hardware_usage(topology_instance)

    @check_authentication
    def get_flavors_dict(self) -> dict:
        """
        Gets flavors defined in OpenStack project with their vcpu and ram usage as dictionary

        :return: flavors dictionary
        """
        return self.open_stack_proxy.get_flavors_dict()

    @check_authentication
    def get_project_limits(self) -> Limits:
        """
        Get Absolute limits of OpenStack project.

        :return Limits of project
        """
        return self.open_stack_proxy.get_project_limits(self.session.get_project_id())
