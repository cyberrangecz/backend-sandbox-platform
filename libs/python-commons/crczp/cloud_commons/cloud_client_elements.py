"""Cloud client elements module for wrapping cloud resource data."""

from dataclasses import dataclass
from typing import Union

from typing_extensions import override

from crczp.cloud_commons.exceptions import CrczpException


class Image:
    """
    Used to wrap image parameters.
    """

    def __init__(
        self,
        os_distro: Union[str, None],
        os_type: Union[str, None],
        disk_format: Union[str, None],
        container_format: Union[str, None],
        visibility: Union[str, None],
        size: Union[int, None],
        status: Union[str, None],
        min_ram: Union[int, None],
        min_disk: Union[int, None],
        created_at: Union[str, None],
        updated_at: Union[str, None],
        tags: list[str],
        default_user: Union[str, None],
        name: Union[str, None],
        owner_specified: dict[str, str],
    ):
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

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Image):
            return NotImplemented

        return (
            self.os_distro == other.os_distro
            and self.os_type == other.os_type
            and self.disk_format == other.disk_format
            and self.container_format == other.container_format
            and self.visibility == other.visibility
            and self.size == other.size
            and self.status == other.status
            and self.min_ram == other.min_ram
            and self.min_disk == other.min_disk
            and self.created_at == other.created_at
            and self.updated_at == other.updated_at
            and self.tags == other.tags
            and self.default_user == other.default_user
            and self.name == other.name
            and self.owner_specified == other.owner_specified
        )

    @override
    def __repr__(self) -> str:
        return (
            '<Image\n'
            f'    os_distro: {self.os_distro},\n'
            f'    os_type: {self.os_type},\n'
            f'    disk_format: {self.disk_format},\n'
            f'    container_format: {self.container_format},\n'
            f'    size: {self.size},\n'
            f'    visibility: {self.visibility},\n'
            f'    status: {self.status},\n'
            f'    min_ram: {self.min_ram},\n'
            f'    min_disk: {self.min_disk},\n'
            f'    created_at: {self.created_at},\n'
            f'    updated_at: {self.updated_at},\n'
            f'    tags: {self.tags},\n'
            f'    default_user: {self.default_user},\n'
            f'    name: {self.name},\n'
            f'    owner_specified: {self.owner_specified}>'
        )


@dataclass
class Limits:
    """
    Used to wrap Absolute Limits of Cloud project
    """

    vcpu: int
    ram: float
    instances: int
    network: int
    subnet: int
    port: int


@dataclass
class Quota:
    """
    Used to wrap quotas parameters of resource.
    """

    limit: float
    in_use: float

    def check_limit(self, requested: float, resource_name: str) -> None:
        """Check if requested amount exceeds the quota limit."""
        required = self.in_use + requested
        if required > self.limit:
            raise CrczpException(
                f'Cloud limits will be exceeded (required: {required},'
                f' maximum: {self.limit} [{resource_name}]).'
            )


class QuotaSet:
    """
    Used to wrap quotas of multiple resources.
    """

    def __init__(
        self, vcpu: Quota, ram: Quota, instances: Quota, network: Quota, subnet: Quota, port: Quota
    ):
        self.vcpu = vcpu
        self.ram = ram
        self.instances = instances
        self.network = network
        self.subnet = subnet
        self.port = port

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, QuotaSet):
            return NotImplemented

        return (
            self.vcpu == other.vcpu
            and self.ram == other.ram
            and self.instances == other.instances
            and self.network == other.network
            and self.subnet == other.subnet
            and self.port == other.port
        )

    def check_limits(self, hardware_usage: 'HardwareUsage') -> None:
        """Check all quota limits against hardware usage."""
        self.vcpu.check_limit(hardware_usage.vcpu, 'vcpu')
        self.ram.check_limit(hardware_usage.ram, 'ram')
        self.instances.check_limit(hardware_usage.instances, 'instances')
        self.network.check_limit(hardware_usage.network, 'network')
        self.subnet.check_limit(hardware_usage.subnet, 'subnet')
        self.port.check_limit(hardware_usage.port, 'port')


@dataclass
class HardwareUsage:
    """
    Used to wrap HeatStacks hardware usage.

    All fields are float: an absolute usage is whole, but __truediv__ turns the same
    object into a fraction of the project Limits (see the DecimalField-based API
    serializers), so none of the fields is guaranteed to be an integer.
    """

    vcpu: float
    ram: float
    instances: float
    network: float
    subnet: float
    port: float

    def __mul__(self, other: int) -> 'HardwareUsage':
        if not isinstance(other, int):
            return NotImplemented

        return HardwareUsage(
            self.vcpu * other,
            self.ram * other,
            self.instances * other,
            self.network * other,
            self.subnet * other,
            self.port * other,
        )

    def __truediv__(self, other: Limits) -> 'HardwareUsage':
        if not isinstance(other, Limits):
            return NotImplemented

        return HardwareUsage(**{
            key: round(value / (other.__dict__[key]), 3) for (key, value) in self.__dict__.items()
        })

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HardwareUsage):
            return NotImplemented

        return (
            self.vcpu == other.vcpu
            and self.ram == other.ram
            and self.instances == other.instances
            and self.network == other.network
            and self.subnet == other.subnet
            and self.port == other.port
        )


@dataclass
class NodeDetails:
    """
    Defines node (Terraform resource) detail
    """

    image_id: str
    status: str
    flavor: str
