from abc import ABC, abstractmethod
from typing import List

from kypo.topology_definition.models import TopologyDefinition
from kypo.commons.cloud_client_elements import Image, QuotaSet, HardwareUsage, Limits


class KypoCloudClientBase(ABC):
    @abstractmethod
    def create_terraform_template(self, topology_definition: TopologyDefinition, *args, **kwargs)\
            -> str:
        pass

    @abstractmethod
    def list_images(self) -> List[Image]:
        pass

    @abstractmethod
    def get_image(self, image_id: int) -> Image:
        pass

    @abstractmethod
    def resume_node(self, resource_id: int):
        pass

    @abstractmethod
    def start_node(self, resource_id: int):
        pass

    @abstractmethod
    def reboot_node(self, resource_id: int):
        pass

    @abstractmethod
    def get_console_url(self, resource_id: str, console_type: str) -> str:
        pass

    @abstractmethod
    def create_keypair(self, name: str, public_key: str = None, key_type: str = 'ssh'):
        pass

    @abstractmethod
    def get_keypair(self, name: str):
        pass

    @abstractmethod
    def delete_keypair(self, name: str):
        pass

    @abstractmethod
    def get_quota_set(self) -> QuotaSet:
        pass

    @abstractmethod
    def get_project_name(self) -> str:
        pass

    @abstractmethod
    def get_hardware_usage(self, topology_instance) -> HardwareUsage:
        pass

    @abstractmethod
    def get_project_limits(self) -> Limits:
        pass
