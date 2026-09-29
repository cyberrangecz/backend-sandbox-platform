"""
Serializers for ostack_proxy_elements classes.
"""

from typing import Any

from rest_framework import serializers

from crczp.cloud_commons import Image

OPENSTACK_OWNER_SPECIFIED_PREFIX = 'owner_specified.openstack.'


class QuotaSerializer(serializers.Serializer[Any]):
    """Serializer for a single OpenStack quota value."""

    limit = serializers.FloatField()
    in_use = serializers.FloatField()


class QuotaSetSerializer(serializers.Serializer[Any]):
    """Serializer for a complete set of OpenStack project quotas."""

    vcpu = QuotaSerializer()
    ram = QuotaSerializer()
    instances = QuotaSerializer()
    network = QuotaSerializer()
    subnet = QuotaSerializer()
    port = QuotaSerializer()


class ProjectInfoSerializer(serializers.Serializer[Any]):
    """Serializer for a project's name and its quota set."""

    project_name = serializers.CharField()
    quotas = QuotaSetSerializer()


class ImageSerializer(serializers.Serializer[Any]):
    """Serializer for an OpenStack image."""

    os_distro = serializers.CharField()
    os_type = serializers.CharField()
    disk_format = serializers.CharField()
    container_format = serializers.CharField()
    visibility = serializers.CharField()
    size = serializers.SerializerMethodField()
    status = serializers.CharField()
    min_ram = serializers.IntegerField()
    min_disk = serializers.IntegerField()
    created_at = serializers.CharField()
    updated_at = serializers.CharField()
    tags = serializers.ListField()
    default_user = serializers.CharField()
    name = serializers.CharField()
    owner_specified = serializers.SerializerMethodField()

    @staticmethod
    def get_size(obj: Image) -> float | None:
        """Return image size in GiB, or None if not set."""
        if obj.size is None:
            return None
        return float(obj.size) / 1024**3

    @staticmethod
    def get_owner_specified(obj: Image) -> dict[str, Any]:
        """Return owner_specified metadata with the OpenStack prefix stripped."""
        return {
            (
                key[len(OPENSTACK_OWNER_SPECIFIED_PREFIX) :]
                if key.startswith(OPENSTACK_OWNER_SPECIFIED_PREFIX)
                else key
            ): value
            for (key, value) in obj.owner_specified.items()
        }


class FlavorResourcesSerializer(serializers.Serializer[Any]):
    """Serializer for the compute resources of a cloud flavor."""

    vcpu = serializers.IntegerField(help_text='Number of virtual CPUs the flavor provides.')
    ram_gb = serializers.FloatField(help_text='RAM the flavor provides, in GB.')


class FlavorSerializer(serializers.Serializer[Any]):
    """Serializer for a cloud flavor with its flavor mapping aliases."""

    flavor = serializers.CharField(
        help_text='Name of the flavor in the cloud project, usable as the flavor of a host or '
        'router in a topology definition.'
    )
    aliases = serializers.ListField(
        child=serializers.CharField(),
        help_text='Names the flavor mapping of the deployment translates to this flavor, each '
        'usable in a topology definition in place of the flavor name; sorted ascending, empty '
        'when none maps to it.',
    )
    resources = FlavorResourcesSerializer(help_text='Compute resources the flavor provides.')


class FlavorCatalogSerializer(serializers.Serializer[Any]):
    """Serializer for the cloud flavors and the flavor mapping aliases mapped to none of them."""

    flavors = FlavorSerializer(
        many=True, help_text='Flavors the cloud project offers, sorted by flavor name ascending.'
    )
    unmapped_aliases = serializers.ListField(
        child=serializers.CharField(),
        help_text='Names the flavor mapping of the deployment translates to a flavor the cloud '
        'project does not offer; a topology definition giving one fails validation. Sorted '
        'ascending, empty when every alias maps to an offered flavor.',
    )


class ProjectLimitsSerializer(serializers.Serializer[Any]):
    """Serializer for OpenStack project absolute limits."""

    vcpu = serializers.IntegerField()
    ram = serializers.FloatField()
    instances = serializers.IntegerField()
    network = serializers.IntegerField()
    subnet = serializers.IntegerField()
    port = serializers.IntegerField()
