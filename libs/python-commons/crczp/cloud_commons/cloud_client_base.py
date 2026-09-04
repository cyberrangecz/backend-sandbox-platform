"""Cloud client base module defining abstract cloud client interface."""

from abc import ABC, abstractmethod
from typing import Any, Optional

from crczp.cloud_commons.cloud_client_elements import (
    HardwareUsage,
    Image,
    Limits,
    NodeDetails,
    QuotaSet,
)
from crczp.cloud_commons.topology_instance import TopologyInstance


class CrczpCloudClientBase(ABC):
    """
    Base class for CyberRangeCZ Platform cloud clients.
    """

    @staticmethod
    @abstractmethod
    def get_private_ip(instance_attrs: dict[str, Any]) -> str:
        """
        Get IP address of a Terraform instance.

        :param instance_attrs: Terraform instance attributes
        :return: IP address
        """

    @abstractmethod
    def get_terraform_provider(self) -> str:
        """
        Get OpenStack Terraform provider
        :return: Terraform provider template
        :raise InvalidTopologyDefinition: Terraform provider template is incorrect
        """

    @abstractmethod
    def create_terraform_template(
        self, topology_instance: TopologyInstance, *args: Any, **kwargs: Any
    ) -> str:
        """
        Create terraform template that will be deployed.

        :param topology_instance: TopologyInstance used to create template
        :keyword key_pair_name_ssh: The name of SSH key pair in the cloud
        :keyword key_pair_name_cert: The name of certificate key pair in the cloud
        :keyword resource_prefix: The prefix of all resources
        :return: Terraform template as a string
        :raise CrczpException: Network validation error
        :raise InvalidTopologyDefinition: Template rendering error
        """

    @abstractmethod
    def list_images(self) -> list[Image]:
        """
        List all available images on the cloud project.

        :return: List of Image objects.
        """

    @abstractmethod
    def get_image(self, image_id: str) -> Image:
        """
        Get Image object based on its ID.

        :param image_id: The ID of image on the cloud
        :return: Image object
        """

    @abstractmethod
    def resume_node(self, node_id: str) -> None:
        """
        Resume node.

        :param node_id: The ID of the node
        :return: None
        :raise CrczpException: Node not found
        """

    @abstractmethod
    def start_node(self, node_id: str) -> None:
        """
        Start node.

        :param node_id: The ID of the node
        :return: None
        :raise CrczpException: Node not found
        """

    @abstractmethod
    def reboot_node(self, node_id: str) -> None:
        """
        Reboot node.

        :param node_id: The ID of the node
        :return: None
        :raise CrczpException: Node not found
        """

    @abstractmethod
    def get_node_details(self, terraform_attrs: dict[str, Any]) -> NodeDetails:
        """
        Get node details from the Terraform resource attributes.
        Note, Terraform attributes are backend specific.

        :param terraform_attrs: Terraform resource attributes of the node
        :return: Node details
        """

    @abstractmethod
    def get_console_url(self, node_id: str, console_type: str) -> str:
        """
        Get console for given node.

        :param node_id: The ID of the node
        :param console_type: Type can be novnc, xvpvnc, spice-html5, rdp-html5, serial and webmks
        :return: Console url
        :raise CrczpException: Node not found
        """

    @abstractmethod
    def create_keypair(
        self, name: str, public_key: Optional[str] = None, key_type: str = 'ssh'
    ) -> None:
        """
        Create key pair in cloud.

        :param name: Name of the key pair
        :param public_key: SSH public key or certificate, it None new is created
        :param key_type: Accepted vales are 'ssh' and 'x509'. Is used as suffix to 'name' parameter
        :return: None
        :raise CrczpException: Creation failure
        """

    @abstractmethod
    def get_keypair(self, name: str) -> Any:
        """
        Get KeyPair instance from cloud.

        :param name: The name of key pair
        :return: KeyPair instance
        :raise CrczpException: Key pair does not exist
        """

    @abstractmethod
    def delete_keypair(self, name: str) -> None:
        """
        Delete key pair.

        :param name: The name of key pair
        :return: None
        :raise CrczpException: Key pair does not exist
        """

    @abstractmethod
    def get_quota_set(self) -> QuotaSet:
        """
        Get quota set of cloud project.

        :return: QuotaSet object
        """

    @abstractmethod
    def get_project_name(self) -> str:
        """
        Get project name from application credentials.

        :return: The name of the cloud project
        """

    @abstractmethod
    def get_hardware_usage(self, topology_instance: TopologyInstance) -> HardwareUsage:
        """
        Get hardware usage of a single sandbox.

        :param topology_instance: Topology instance from which the sandbox is created
        :return: HardwareUsage object
        """

    @abstractmethod
    def get_flavors_dict(self) -> dict[str, Any]:
        """
        Gets flavors defined in OpenStack project with their vcpu and ram usage as dictionary

        :return: flavors dictionary
        """

    @abstractmethod
    def get_project_limits(self) -> Limits:
        """
        Get resources limits of cloud project.

        :return: Limits object
        """
