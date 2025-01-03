from typing import Union, Dict, List
from crczp.cloud_commons.exceptions import CrczpException


class Image:
    """
    Used to wrap image parameters.
    """

    def __init__(self, os_distro: Union[str, None], os_type: Union[str, None],
                 disk_format: Union[str, None], container_format: Union[str, None],
                 visibility: Union[str, None], size: Union[int, None], status: Union[str, None],
                 min_ram: Union[int, None], min_disk: Union[int, None],
                 created_at: Union[str, None], updated_at: Union[str, None], tags: List[str],
                 default_user: Union[str, None], name: Union[str, None],
                 owner_specified: Dict[str, str]):
        self.os_distro = os_distro
        self.os_type = os_type
        self.disk_format = disk_format
        self.container_format = container_format
        self.visibility = visibility
        self.size = size
        self.status = status
        self.min_ram = min_ram
        self.min_disk = min_disk
        self.created_at = created_at
        self.updated_at = updated_at
        self.tags = tags
        self.default_user = default_user
        self.name = name
        self.owner_specified = owner_specified

    def __eq__(self, other: 'Image') -> bool:
        if not isinstance(other, Image):
            return NotImplemented

        return self.os_distro == other.os_distro and self.os_type == other.os_type and \
            self.disk_format == other.disk_format and \
            self.container_format == other.container_format and \
            self.visibility == other.visibility and self.size == other.size and \
            self.status == other.status and self.min_ram == other.min_ram and \
            self.min_disk == other.min_disk and self.created_at == other.created_at and \
            self.updated_at == other.updated_at and self.tags == other.tags and \
            self.default_user == other.default_user and self.name == other.name and \
            self.owner_specified == other.owner_specified

    def __repr__(self):
        return "<Image\n" \
               "    os_distro: {0.os_distro},\n" \
               "    os_type: {0.os_type},\n" \
               "    disk_format: {0.disk_format},\n" \
               "    container_format: {0.container_format},\n" \
               "    size: {0.size},\n" \
               "    visibility: {0.visibility},\n" \
               "    status: {0.status},\n" \
               "    min_ram: {0.min_ram},\n" \
               "    min_disk: {0.min_disk},\n" \
               "    created_at: {0.created_at},\n" \
               "    updated_at: {0.updated_at},\n" \
               "    tags: {0.tags},\n" \
               "    default_user: {0.default_user},\n" \
               "    name: {0.name},\n" \
               "    owner_specified: {0.owner_specified}>".format(self)


class Limits:
    """
    Used to wrap Absolute Limits of Cloud project
    """

    def __init__(self, vcpu: int, ram: float, instances: int, network: int, subnet: int, port: int):
        self.vcpu = vcpu
        self.ram = ram
        self.instances = instances
        self.network = network
        self.subnet = subnet
        self.port = port


class Quota:
    """
    Used to wrap quotas parameters of resource.
    """

    def __init__(self, limit: float, in_use: float):
        self.limit = limit
        self.in_use = in_use

    def __eq__(self, other: 'Quota') -> bool:
        if not isinstance(other, Quota):
            return NotImplemented

        return self.limit == other.limit and self.in_use == other.in_use

    def check_limit(self, requested: int, resource_name: str):
        required = self.in_use + requested
        if required > self.limit:
            raise CrczpException(f'Cloud limits will be exceeded (required: {required},'
                                f' maximum: {self.limit} [{resource_name}]).')


class QuotaSet:
    """
    Used to wrap quotas of multiple resources.
    """

    def __init__(self, vcpu: Quota, ram: Quota, instances: Quota, network: Quota,
                 subnet: Quota, port: Quota):
        self.vcpu = vcpu
        self.ram = ram
        self.instances = instances
        self.network = network
        self.subnet = subnet
        self.port = port

    def __eq__(self, other: 'QuotaSet') -> bool:
        if not isinstance(other, QuotaSet):
            return NotImplemented

        return self.vcpu == other.vcpu and self.ram == other.ram and \
            self.instances == other.instances and self.network == other.network and \
            self.subnet == other.subnet and self.port == other.port

    def check_limits(self, hardware_usage: 'HardwareUsage'):
        self.vcpu.check_limit(hardware_usage.vcpu, 'vcpu')
        self.ram.check_limit(hardware_usage.ram, 'ram')
        self.instances.check_limit(hardware_usage.instances, 'instances')
        self.network.check_limit(hardware_usage.network, 'network')
        self.subnet.check_limit(hardware_usage.subnet, 'subnet')
        self.port.check_limit(hardware_usage.port, 'port')


class HardwareUsage:
    """
    Used to wrap HeatStacks hardware usage.
    """

    def __init__(self, vcpu, ram, instances, network, subnet, port):
        self.vcpu = vcpu
        self.ram = ram
        self.instances = instances
        self.network = network
        self.subnet = subnet
        self.port = port

    def __mul__(self, other):
        if not isinstance(other, int):
            return NotImplemented

        return HardwareUsage(self.vcpu*other, self.ram*other, self.instances*other,
                             self.network*other, self.subnet*other, self.port*other)

    def __truediv__(self, other):
        if not isinstance(other, Limits):
            return NotImplemented

        return HardwareUsage(**{key: round(value / (other.__dict__[key]), 3) for (key, value)
                                in self.__dict__.items()})

    def __eq__(self, other: 'HardwareUsage'):
        if not isinstance(other, HardwareUsage):
            return NotImplemented

        return self.vcpu == other.vcpu and self.ram == other.ram and\
            self.instances == other.instances and self.network == other.network and\
            self.subnet == other.subnet and self.port == other.port


class NodeDetails:
    """
    Defines node (Terraform resource) detail
    """

    def __init__(self, image_id: str, status: str, flavor: str) -> None:
        self.image_id = image_id
        self.status = status
        self.flavor = flavor
