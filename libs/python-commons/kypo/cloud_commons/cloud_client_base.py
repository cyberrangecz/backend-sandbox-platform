from abc import ABC, abstractmethod
from typing import List

from kypo.cloud_commons.cloud_client_elements import Image, QuotaSet, HardwareUsage, Limits
from kypo.cloud_commons.topology_instance import TopologyInstance


class KypoCloudClientBase(ABC):
    """
    Base class for KYPO cloud clients.
    """
    @abstractmethod
    def create_terraform_template(self, topology_instance: TopologyInstance, *args, **kwargs)\
            -> str:
        """
        Create terraform template that will be deployed.

        :param topology_instance: TopologyInstance used to create template
        :keyword key_pair_name_ssh: The name of SSH key pair in the cloud
        :keyword key_pair_name_cert: The name of certificate key pair in the cloud
        :keyword resource_prefix: The prefix of all resources
        :return: Terraform template as a string
        :raise KypoException: Network validation error
        :raise InvalidTopologyDefinition: Template rendering error
        """
        pass

    @abstractmethod
    def list_images(self) -> List[Image]:
        """
        List all available images on the cloud project.

        :return: List of Image objects.
        """
        pass

    @abstractmethod
    def get_image(self, image_id: str) -> Image:
        """
        Get Image object based on its ID.

        :param image_id: The ID of image on the cloud
        :return: Image object
        """
        pass

    @abstractmethod
    def resume_node(self, node_id: str) -> None:
        """
        Resume node.

        :param node_id: The ID of the node
        :return: None
        :raise KypoException: Node not found
        """
        pass

    @abstractmethod
    def start_node(self, node_id: str) -> None:
        """
        Start node.

        :param node_id: The ID of the node
        :return: None
        :raise KypoException: Node not found
        """
        pass

    @abstractmethod
    def reboot_node(self, node_id: str) -> None:
        """
        Reboot node.

        :param node_id: The ID of the node
        :return: None
        :raise KypoException: Node not found
        """
        pass

    @abstractmethod
    def get_console_url(self, node_id: str, console_type: str) -> str:
        """
        Get console for given node.

        :param node_id: The ID of the node
        :param console_type: Type can be novnc, xvpvnc, spice-html5, rdp-html5, serial and webmks
        :return: Console url
        :raise KypoException: Node not found
        """
        pass

    @abstractmethod
    def create_keypair(self, name: str, public_key: str = None, key_type: str = 'ssh') -> None:
        """
        Create key pair in cloud.

        :param name: Name of the key pair
        :param public_key: SSH public key or certificate, it None new is created
        :param key_type: Accepted vales are 'ssh' and 'x509'. Is used as suffix to 'name' parameter
        :return: None
        :raise KypoException: Creation failure
        """
        pass

    @abstractmethod
    def get_keypair(self, name: str):
        """
        Get KeyPair instance from cloud.

        :param name: The name of key pair
        :return: KeyPair instance
        :raise KypoException: Key pair does not exist
        """
        pass

    @abstractmethod
    def delete_keypair(self, name: str) -> None:
        """
        Delete key pair.

        :param name: The name of key pair
        :return: None
        :raise KypoException: Key pair does not exist
        """
        pass

    @abstractmethod
    def get_quota_set(self) -> QuotaSet:
        """
        Get quota set of cloud project.

        :return: QuotaSet object
        """
        pass

    @abstractmethod
    def get_project_name(self) -> str:
        """
        Get project name from application credentials.

        :return: The name of the cloud project
        """
        pass

    @abstractmethod
    def get_hardware_usage(self, topology_instance) -> HardwareUsage:
        """
        Get hardware usage of a single sandbox.

        :param topology_instance: Topology instance from which the sandbox is created
        :return: HardwareUsage object
        """
        pass

    @abstractmethod
    def get_project_limits(self) -> Limits:
        """
        Get resources limits of cloud project.

        :return: Limits object
        """
        pass
