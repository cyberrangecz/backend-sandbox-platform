"""
Serializer and validator definitions for database models.

Serializers are used to deserialize (parse) requests
and serialize database queries to responses.
Validators validate single fields or entire objects.

Swagger can utilise type hints to determine type, so use them in your own methods.
"""

from __future__ import annotations

from typing import Any, override

from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from crczp.sandbox_cloud_app import serializers as cloud_serializers
from crczp.sandbox_common_lib.serializers import UserSerializer
from crczp.sandbox_definition_app.models import Definition
from crczp.sandbox_definition_app.serializers import DefinitionSerializer
from crczp.sandbox_instance_app import models
from crczp.sandbox_instance_app.lib import pools, requests


class PoolSerializer(serializers.ModelSerializer[models.Pool]):
    """Serializer for Pool model."""

    size = serializers.SerializerMethodField(
        help_text='Number of allocation units associated with this pool.'
    )
    lock_id = serializers.SerializerMethodField()
    definition = serializers.SerializerMethodField()
    definition_id = serializers.PrimaryKeyRelatedField(
        source='definition', queryset=Definition.objects.all(), write_only=True
    )
    created_by = serializers.SerializerMethodField()
    hardware_usage = serializers.SerializerMethodField()

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for PoolSerializer."""

        model = models.Pool
        fields = (
            'id',
            'definition_id',
            'size',
            'max_size',
            'lock_id',
            'rev',
            'rev_sha',
            'comment',
            'visible',
            'created_by',
            'hardware_usage',
            'definition',
            'send_emails',
        )
        read_only_fields = (
            'id',
            'definition_id',
            'size',
            'lock',
            'rev',
            'rev_sha',
            'created_by',
            'hardware_usage',
            'definition',
        )

    @override
    def update(self, instance: models.Pool, validated_data: Any) -> models.Pool:
        """Update pool fields from validated data."""
        instance.max_size = validated_data.get('max_size', instance.max_size)
        instance.comment = validated_data.get('comment', instance.comment)
        instance.visible = validated_data.get('visible', instance.visible)
        instance.send_emails = validated_data.get('send_emails', instance.send_emails)
        instance.save()
        return instance

    @staticmethod
    def validate_max_size(value: int) -> int:
        """Validate that max_size is greater than 0"""
        if value < 1:
            raise serializers.ValidationError(
                f'Pool max_size value must be greater than 0. Your value: {value}.'
            )
        return value

    @staticmethod
    def get_size(pool: models.Pool) -> int:
        """Return pool size, recalculating from allocation units if currently zero."""
        # Required only because of the migration from older versions.
        # For the release after 23.12 remove this method and set size attribute of serialize to
        # IntegerField
        if pool.size == 0:
            with transaction.atomic():
                pool = models.Pool.objects.select_for_update().get(id=pool.id)
                pool.size = pool.allocation_units.count()
                pool.save()

        return pool.size

    @staticmethod
    def get_lock_id(obj: models.Pool) -> int | None:
        """Return the pool lock id, or None if not locked."""
        return obj.lock.id if hasattr(obj, 'lock') else None

    @extend_schema_field(UserSerializer())
    @staticmethod
    def get_created_by(obj: models.Pool) -> Any:
        """Return serialized created_by user data."""
        return UserSerializer(obj.created_by).data

    @extend_schema_field(field=serializers.BooleanField())
    @staticmethod
    def get_hardware_usage(obj: models.Pool) -> Any:
        """Return serialized hardware usage data for the pool."""
        hardware_usage = pools.get_hardware_usage_of_sandbox(obj)
        return HardwareUsageSerializer(hardware_usage).data

    @extend_schema_field(DefinitionSerializer())
    @staticmethod
    def get_definition(obj: models.Pool) -> Any:
        """Return serialized definition data for the pool."""
        return DefinitionSerializer(obj.definition).data


class PoolSerializerCreate(PoolSerializer):
    """Serializer for creating a new Pool."""

    class Meta(PoolSerializer.Meta):  # pylint: disable=too-few-public-methods
        """Meta options for PoolSerializerCreate."""

        read_only_fields = ('id', 'size')


class RequestSerializer(serializers.ModelSerializer[Any]):
    """Base serializer for allocation and cleanup request models."""

    allocation_unit_id = serializers.PrimaryKeyRelatedField(
        source='allocation_unit', read_only=True
    )
    stages = serializers.SerializerMethodField()

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for RequestSerializer."""

        fields = ('id', 'allocation_unit_id', 'created', 'stages')
        read_only_fields = ('id', 'allocation_unit_id', 'created', 'stages')


class AllocationRequestSerializer(RequestSerializer):
    """Serializer for AllocationRequest model."""

    @extend_schema_field(field=serializers.ListField(child=serializers.CharField()))
    @staticmethod
    def get_stages(obj: Any) -> list[str]:
        """Return the allocation request stages completion state."""
        return requests.get_allocation_request_stages_state(obj)

    class Meta(RequestSerializer.Meta):  # pylint: disable=too-few-public-methods
        """Meta options for AllocationRequestSerializer."""

        model = models.AllocationRequest


class CleanupRequestSerializer(RequestSerializer):
    """Serializer for CleanupRequest model."""

    @extend_schema_field(field=serializers.ListField(child=serializers.CharField()))
    @staticmethod
    def get_stages(obj: Any) -> list[str]:
        """Return the cleanup request stages completion state."""
        return requests.get_cleanup_request_stages_state(obj)

    class Meta(RequestSerializer.Meta):  # pylint: disable=too-few-public-methods
        """Meta options for CleanupRequestSerializer."""

        model = models.CleanupRequest


class PoolCleanupRequestSerializer(serializers.Serializer[Any]):
    """Serializer for pool cleanup request input."""

    pool_id = serializers.IntegerField()
    reason = serializers.CharField(required=False)


class PoolCleanupResultSerializer(serializers.Serializer[Any]):
    """Result of cleaning up several allocation units of a pool."""

    skipped_unit_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text='Units a forced cleanup skipped because they cannot be cleaned up yet: their '
        'first stage is running, or their allocation is queued. Retry them later.',
    )


class CancelledCountSerializer(serializers.Serializer[Any]):
    """Result of cancelling a pool's queued allocations."""

    cancelled_count = serializers.IntegerField(help_text='Number of removed allocation units.')


class ForceCancelledCountSerializer(serializers.Serializer[Any]):
    """Result of force-cancelling a pool's running allocations."""

    force_cancelled_count = serializers.IntegerField(
        help_text='Number of removed allocation units.'
    )


class ForceCleanedCountSerializer(serializers.Serializer[Any]):
    """Result of force-removing a pool's units with an unfinished cleanup."""

    force_cleaned_count = serializers.IntegerField(help_text='Number of removed allocation units.')


class PoolCleanupRequestFailedSerializer(serializers.Serializer[Any]):
    """Serializer for failed pool cleanup request data."""

    pool_id = serializers.IntegerField()
    error_message = serializers.CharField()


class SandboxAllocationUnitSerializer(serializers.ModelSerializer[models.SandboxAllocationUnit]):
    """Serializer for SandboxAllocationUnit model."""

    allocation_request = AllocationRequestSerializer(read_only=True)
    cleanup_request = CleanupRequestSerializer(read_only=True, allow_null=True)
    pool_id = serializers.PrimaryKeyRelatedField(source='pool', read_only=True)
    created_by = serializers.SerializerMethodField()
    locked = serializers.SerializerMethodField()
    sandbox_id = serializers.SerializerMethodField()

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for SandboxAllocationUnitSerializer."""

        model = models.SandboxAllocationUnit
        fields = (
            'id',
            'pool_id',
            'allocation_request',
            'cleanup_request',
            'created_by',
            'created_by_sub',
            'created_at',
            'locked',
            'sandbox_id',
            'comment',
        )
        read_only_fields = (
            'id',
            'pool_id',
            'allocation_request',
            'cleanup_request',
            'created_by',
            'created_by_sub',
            'created_at',
            'locked',
            'sandbox_id',
        )

    @override
    def update(
        self, instance: models.SandboxAllocationUnit, validated_data: Any
    ) -> models.SandboxAllocationUnit:
        """Update allocation unit comment from validated data."""
        instance.comment = validated_data.get('comment', instance.comment)
        instance.save()
        return instance

    @extend_schema_field(UserSerializer())
    @staticmethod
    def get_created_by(obj: models.SandboxAllocationUnit) -> Any:
        """Return serialized created_by user data."""
        return UserSerializer(obj.created_by).data

    @extend_schema_field(field=serializers.BooleanField())
    @staticmethod
    def get_locked(obj: models.SandboxAllocationUnit) -> bool:
        """Return True if the sandbox allocation unit has a lock."""
        return hasattr(obj, 'sandbox') and hasattr(obj.sandbox, 'lock')

    @extend_schema_field(field=serializers.CharField(allow_null=True))
    @staticmethod
    def get_sandbox_id(obj: models.SandboxAllocationUnit) -> str | None:
        """Return the id of the unit's sandbox, or None until the sandbox is created."""
        return obj.sandbox.id if hasattr(obj, 'sandbox') else None


class SandboxAllocationUnitIdListSerializer(serializers.Serializer[Any]):
    """Serializer for a list of sandbox allocation unit IDs."""

    unit_ids = serializers.ListField(child=serializers.IntegerField())


class AllocationUnitByCreatorQuerySerializer(serializers.Serializer[Any]):
    """Query parameters of the listing of allocation units by creator."""

    created_by_sub = serializers.CharField(
        max_length=255, help_text='OIDC sub of the trainee who allocated the units.'
    )
    state = serializers.ChoiceField(
        choices=['ACTIVE'],
        required=False,
        help_text='ACTIVE lists only units that hold or are building a sandbox: their '
        'allocation has not failed, and they are not cleaned up or their cleanup is running.',
    )


class TerraformAllocationStageSerializer(serializers.ModelSerializer[models.StackAllocationStage]):
    """Serializer for StackAllocationStage model."""

    request_id = serializers.PrimaryKeyRelatedField(source='allocation_request', read_only=True)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for TerraformAllocationStageSerializer."""

        model = models.StackAllocationStage
        fields = (
            'id',
            'request_id',
            'start',
            'end',
            'failed',
            'error_message',
            'status',
            'status_reason',
        )
        read_only_fields = fields


class TerraformCleanupStageSerializer(serializers.ModelSerializer[models.StackCleanupStage]):
    """Serializer for StackCleanupStage model."""

    request_id = serializers.PrimaryKeyRelatedField(source='cleanup_request', read_only=True)
    # allocation_stage_id = serializers.PrimaryKeyRelatedField(
    #    source='allocation_stage', read_only=True)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for TerraformCleanupStageSerializer."""

        model = models.StackCleanupStage
        fields = (
            'id',
            'request_id',
            'start',
            'end',
            'failed',
            'error_message',
            # 'allocation_stage_id',
        )
        read_only_fields = fields


class SandboxSerializer(serializers.ModelSerializer[models.Sandbox]):
    """Serializer for Sandbox model."""

    lock_id = serializers.SerializerMethodField()
    allocation_unit_id = serializers.PrimaryKeyRelatedField(
        source='allocation_unit', read_only=True
    )

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for SandboxSerializer."""

        model = models.Sandbox
        fields = ('id', 'lock_id', 'allocation_unit_id', 'ready')
        read_only_fields = ('id', 'lock', 'allocation_unit_id', 'ready')

    @staticmethod
    def get_lock_id(obj: models.Sandbox) -> int | None:
        """Return the sandbox lock id, or None if not locked."""
        return obj.lock.id if hasattr(obj, 'lock') else None


class SandboxLockSerializer(serializers.ModelSerializer[models.SandboxLock]):
    """Serializer for SandboxLock model."""

    sandbox_id = serializers.PrimaryKeyRelatedField(source='sandbox', read_only=True)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for SandboxLockSerializer."""

        model = models.SandboxLock
        fields = ('id', 'sandbox_id', 'created_by')
        read_only_fields = fields


class PoolLockSerializer(serializers.ModelSerializer[models.PoolLock]):
    """Serializer for PoolLock model."""

    pool_id = serializers.PrimaryKeyRelatedField(source='pool', read_only=True)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for PoolLockSerializer."""

        model = models.PoolLock
        fields = ('id', 'pool_id')
        read_only_fields = ('id', 'pool_id')
        write_only_fields = ('training_access_token',)


class NodeActionSerializer(serializers.Serializer[Any]):
    """Serializer for VM node action requests."""

    ACTION_CHOICES = ('suspend', 'resume', 'reboot')
    action = serializers.ChoiceField(
        choices=ACTION_CHOICES, help_text='Action you with to perform on the node.'
    )


##########################################
# CRCZP OpenStack lib classes serializers #
##########################################


class HostSerializer(serializers.Serializer[Any]):
    name = serializers.CharField()
    os_type = serializers.CharField()
    gui_access = serializers.BooleanField()
    is_accessible = serializers.BooleanField()
    ip = serializers.CharField(allow_null=True)


class SubnetSerializer(serializers.Serializer[Any]):
    """CRCZP OS lib Subnet topology serializer."""

    name = serializers.CharField()
    cidr = serializers.CharField()
    hosts = HostSerializer(many=True)


class RouterSerializer(serializers.Serializer[Any]):
    """CRCZP OS lib Host and Router topology serializer"""

    name = serializers.CharField()
    os_type = serializers.CharField()
    gui_access = serializers.BooleanField()
    is_accessible = serializers.BooleanField()
    ip = serializers.CharField(allow_null=True)
    subnets = SubnetSerializer(many=True)


class LibNetworkSerializer(serializers.Serializer[Any]):
    """CRCZP OS lib Network topology serializer"""

    name = serializers.CharField()
    cidr = serializers.CharField()


class TopologySerializer(serializers.Serializer[Any]):
    """Serializer for topology"""

    routers = RouterSerializer(many=True)


class NodeSerializer(serializers.Serializer[Any]):
    """CRCZP OS lib Instance serializer"""

    name = serializers.CharField()
    id = serializers.CharField()
    status = serializers.CharField()
    image = cloud_serializers.ImageSerializer()
    flavor_name = serializers.CharField()


class NodeConsoleSerializer(serializers.Serializer[Any]):
    """Serializer for a VM console URL."""

    url = serializers.CharField()


class SandboxEventSerializer(serializers.Serializer[Any]):
    """Serializer for a sandbox stack event."""

    time = serializers.CharField(source='event_time')
    name = serializers.CharField(source='resource_name')
    status = serializers.CharField(source='resource_status')
    status_reason = serializers.CharField(source='resource_status_reason')


class SandboxResourceSerializer(serializers.Serializer[Any]):
    """Serializer for a sandbox stack resource."""

    name = serializers.CharField(source='resource_name')
    type = serializers.CharField(source='resource_type')
    status = serializers.CharField(source='resource_status')


class HardwareUsageSerializer(serializers.Serializer[Any]):
    """Serializer for hardware usage quota data."""

    vcpu = serializers.DecimalField(decimal_places=3, max_digits=7)
    ram = serializers.DecimalField(decimal_places=3, max_digits=7)
    instances = serializers.DecimalField(decimal_places=3, max_digits=7)
    network = serializers.DecimalField(decimal_places=3, max_digits=7)
    subnet = serializers.DecimalField(decimal_places=3, max_digits=7)
    port = serializers.DecimalField(decimal_places=3, max_digits=7)


class ProtocolSerializer(serializers.Serializer[Any]):
    """Serializer for a network protocol with port."""

    name = serializers.CharField()
    port = serializers.IntegerField(max_value=65535)


class NodeAccessDataSerializer(serializers.Serializer[Any]):
    """Serializer for node access data including IP and protocols."""

    man_ip = serializers.CharField()
    man_port = serializers.IntegerField(max_value=65535)
    host_ip = serializers.CharField()
    protocols = ProtocolSerializer(many=True)


class SandboxVpnConfigSerializer(serializers.Serializer[Any]):
    """Serializer for a sandbox's Netbird VPN client configuration."""

    management_url = serializers.CharField()
    setup_key = serializers.CharField(allow_null=True)
    routes = serializers.ListField(child=serializers.CharField())
    command = serializers.CharField(allow_null=True)
