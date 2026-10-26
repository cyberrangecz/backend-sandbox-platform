"""Database models for sandbox instance app."""

import re
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, override

import structlog
from django.conf import settings
from django.contrib.auth.models import User
from django.db import models, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from crczp.sandbox_common_lib import utils
from crczp.sandbox_definition_app.models import Definition
from crczp.sandbox_instance_app.lib.email_notifications import send_email, validate_emails_enabled

if TYPE_CHECKING:
    # Only for the reverse-accessor annotations below; importing at runtime would
    # be circular (sandbox_ansible_app.models imports from this module).
    from crczp.sandbox_ansible_app.models import (
        AllocationAnsibleOutput,
        CleanupAnsibleOutput,
        Container,
        ContainerCleanup,
        NetworkingAnsibleAllocationStage,
        NetworkingAnsibleCleanupStage,
        UserAnsibleAllocationStage,
        UserAnsibleCleanupStage,
    )

DEFAULT_SANDBOX_UUID = '1'
CARRIAGE_RETURN_LINE_BREAK = re.compile(r'\r\n?')
LOG = structlog.get_logger()

ENQUEUE_GRACE = timedelta(minutes=1)
"""How long a requested job may go unqueued before it counts as lost."""


class OrderedByIdModel(models.Model):
    """Abstract base class for models whose rows are ordered by id by default."""

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for OrderedByIdModel."""

        abstract = True
        ordering = ['id']


class StageOutputRow(OrderedByIdModel):
    """
    Abstract base class for one line of the output a stage process wrote.
    Every line break inside the content is stored as a line feed.
    """

    content = models.TextField()

    id: int

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for StageOutputRow."""

        abstract = True

    @override
    def save(self, *args: Any, **kwargs: Any) -> None:
        """Store the row with every carriage return line break turned into a line feed."""
        self.content = CARRIAGE_RETURN_LINE_BREAK.sub('\n', self.content)
        super().save(*args, **kwargs)

    @override
    def __str__(self) -> str:
        return f'ID: {self.id}, CONTENT: {self.content}'


class Pool(models.Model):
    """Represents a pool of sandboxes sharing a common definition and key-pair."""

    # Descriptors Django creates at runtime (implicit pk and reverse relations).
    # Bare annotations only: they declare types for the type checker and are
    # invisible to Django's model machinery, so they never affect the schema.
    id: int
    allocation_units: models.Manager['SandboxAllocationUnit']
    lock: 'PoolLock'

    definition = models.ForeignKey(
        Definition,
        on_delete=models.PROTECT,
    )
    max_size = models.IntegerField(
        help_text='Maximum amount of Allocation Units associated with this pool.'
    )
    size = models.PositiveIntegerField(
        default=0,
        help_text='Current amount of Allocation Units associated with this pool.',
    )
    private_management_key = models.TextField(help_text='Private key for management access.')
    public_management_key = models.TextField(help_text='Public key for management access.')
    management_certificate = models.TextField(
        help_text='Certificate for windows management access.'
    )
    uuid = models.TextField(default=utils.get_simple_uuid)
    rev = models.TextField(help_text='Definition revision used for sandboxes.')
    rev_sha = models.TextField(help_text='SHA of the Definition revision.')
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, help_text='The user that created this pool.'
    )
    send_emails = models.BooleanField(default=False, validators=[validate_emails_enabled])
    comment = models.CharField(
        default='', blank=True, max_length=256, help_text='Comment about specifics of this pool'
    )
    visible = models.BooleanField(
        default=True,
        help_text=(
            'Visibility to other instructors. If False, pool is only visible to owner and admins.'
        ),
    )

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for Pool model."""

        ordering = ['id']

    @override
    def __str__(self) -> str:
        return (
            f'ID: {self.id}, DEFINITION: {self.definition.id}, MAX_SIZE: {self.max_size}, '
            f'REV: {self.rev}'
        )

    def get_pool_prefix(self) -> str:
        """Returns a prefix of this pool."""
        prefix = settings.CRCZP_SERVICE_CONFIG.stack_name_prefix
        return f'{prefix}-p{self.id:010d}'

    def get_keypair_name(self) -> str:
        """Returns a name of the management key-pair for this pool."""
        return f'{self.get_pool_prefix()}-{self.uuid}'

    @property
    def ssh_keypair_name(self) -> str:
        """Return the SSH key-pair name for this pool."""
        return self.get_keypair_name() + '-ssh'

    @property
    def certificate_keypair_name(self) -> str:
        """Return the certificate key-pair name for this pool."""
        return self.get_keypair_name() + '-cert'


class SandboxAllocationUnit(OrderedByIdModel):
    """Represents a single sandbox allocation unit within a pool."""

    id: int
    sandbox: 'Sandbox'
    allocation_request: 'AllocationRequest'
    cleanup_request: 'CleanupRequest'

    pool = models.ForeignKey(
        Pool,
        on_delete=models.PROTECT,
        related_name='allocation_units',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        help_text='The user that created this sandbox allocation unit.',
    )
    created_by_sub = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        default=None,
        help_text='OIDC sub of the trainee who allocated this unit for themselves; '
        'null for units built by an organizer.',
    )
    created_at = models.DateTimeField(
        default=timezone.now, help_text='When this allocation unit was created.'
    )
    comment = models.CharField(
        default='', blank=True, max_length=256, help_text='Comment about specifics of this sandbox'
    )

    class Meta(OrderedByIdModel.Meta):  # pylint: disable=too-few-public-methods
        """Meta options for SandboxAllocationUnit."""

        indexes = [models.Index(fields=['created_by_sub'], name='sau_created_by_sub_idx')]

    @staticmethod
    def has_unfinished_cleanup() -> Exists:
        """
        Expression selecting units whose cleanup is queued or running: their cleanup request
        has no stages yet, or an unfinished one. A failed cleanup is finished.
        """
        return Exists(
            CleanupRequest.objects.filter(
                Q(stages__isnull=True) | Q(stages__finished=False), allocation_unit=OuterRef('pk')
            )
        )

    @staticmethod
    def is_active() -> Q:
        """
        Expression selecting units that hold, or are still building, a sandbox.

        The allocation has not failed, and the unit is either not being cleaned up or its
        cleanup is still queued or running.
        """
        allocation_failed = Exists(
            AllocationStage.objects.filter(
                allocation_request_fk_many__allocation_unit=OuterRef('pk'), failed=True
            )
        )
        has_cleanup = Exists(CleanupRequest.objects.filter(allocation_unit=OuterRef('pk')))
        return ~allocation_failed & (~has_cleanup | SandboxAllocationUnit.has_unfinished_cleanup())

    def get_stack_name(self) -> str:
        """Returns a name of the stack for this sandbox"""
        return f'{self.pool.get_pool_prefix()}-s{self.id:010d}'


class Sandbox(models.Model):
    """Represents an allocated sandbox with user access keys."""

    allocation_unit_id: int
    lock: 'SandboxLock'
    netbird_access: models.Manager['SandboxNetbirdAccess']
    netbird_resources: models.Manager['SandboxNetbirdResources']
    role_keypairs: models.Manager['SandboxRoleKeypair']

    id = models.CharField(
        primary_key=True,
        auto_created=False,
        max_length=36,
        default=DEFAULT_SANDBOX_UUID,
        editable=False,
    )
    allocation_unit = models.OneToOneField(
        SandboxAllocationUnit,
        on_delete=models.PROTECT,
        related_name='sandbox',
    )
    private_user_key = models.TextField(help_text='Private key for user access.')
    public_user_key = models.TextField(help_text='Public key for management access.')
    ready = models.BooleanField(
        default=False, help_text='Is the sandbox ready to use for trainings.'
    )

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for Sandbox model."""

        ordering = ['id']

    @override
    def __str__(self) -> str:
        return (
            f'ID: {self.id}, ALLOCATION_UNIT: {self.allocation_unit.id}, '
            f'LOCK: {self.lock.id if hasattr(self, "lock") else None}'
        )


class SandboxRoleKeypair(models.Model):
    """
    Stores one SSH keypair scoped to one declared role of a sandbox, alongside
    the sandbox's own flat keypair. One row per (sandbox, role).
    """

    id: int

    # Implicit FK attribute; Sandbox has a CharField primary key.
    sandbox_id: str

    sandbox = models.ForeignKey(
        Sandbox,
        on_delete=models.CASCADE,
        related_name='role_keypairs',
        help_text='Sandbox this role keypair belongs to.',
    )
    role = models.CharField(max_length=128, help_text='Declared role this keypair is scoped to.')
    private_key = models.TextField(help_text='Private key for this role.')
    public_key = models.TextField(help_text='Public key for this role.')

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for SandboxRoleKeypair model."""

        ordering = ['id']
        unique_together = [('sandbox', 'role')]

    @override
    def __str__(self) -> str:
        return f'SANDBOX: {self.sandbox_id}, ROLE: {self.role}'


class SandboxLock(OrderedByIdModel):
    """Represents a lock on a sandbox preventing concurrent access."""

    id: int

    sandbox = models.OneToOneField(
        Sandbox,
        on_delete=models.PROTECT,
        related_name='lock',
    )
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, help_text='The user that created this lock.'
    )

    @override
    def __str__(self) -> str:
        return f'ID: {self.id}, Sandbox: {self.sandbox.id}'


class PoolLock(OrderedByIdModel):
    """Represents a lock on a pool used during active training sessions."""

    id: int

    pool = models.OneToOneField(
        Pool,
        on_delete=models.PROTECT,
        related_name='lock',
    )

    training_access_token = models.CharField(max_length=256, default=None, null=True)

    @override
    def __str__(self) -> str:
        return f'ID: {self.id}, Pool: {self.pool.id}'


class SandboxRequest(models.Model):
    """Abstract base class for Sandbox Requests."""

    id: int
    created = models.DateTimeField(default=timezone.now)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for SandboxRequest model."""

        abstract = True
        ordering = ['created', 'id']

    @override
    def __str__(self) -> str:
        return f'ID: {self.id}, CREATED: {self.created}'


class AllocationRequest(SandboxRequest):
    """Represents a request to allocate a sandbox for an allocation unit."""

    stages: models.Manager['AllocationStage']
    stackallocationstage: 'StackAllocationStage'
    networkingansibleallocationstage: 'NetworkingAnsibleAllocationStage'
    useransibleallocationstage: 'UserAnsibleAllocationStage'

    allocation_unit = models.OneToOneField(
        SandboxAllocationUnit,
        on_delete=models.CASCADE,
        related_name='allocation_request',
    )
    netbird_job_id = models.TextField(
        null=True,
        blank=True,
        help_text='The RQ job that provisions NetBird for the sandbox; null once it is settled.',
    )
    request_group = models.ForeignKey(
        'SandboxRequestGroup',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='allocation_requests',
        help_text='The request group the sandbox build counts in; null once the build is '
        'counted, or when it counts in none.',
    )

    @property
    def is_finished(self) -> bool:
        """Whether all stages are finished."""
        return self.stages.filter(finished=False).count() == 0

    @property
    def is_settled(self) -> bool:
        """Whether all stages are finished and the NetBird provisioning job has ended."""
        return self.is_finished and self.netbird_job_id is None

    def count_build_in_request_group(self, failure: Exception | None = None) -> None:
        """
        Count the end of the sandbox build in its request group, as failed when a failure
        is given.

        The request leaves the group as the build is counted, so the build is counted once
        and every later call does nothing.

        :param failure: What made the build fail; None for a build that succeeded.
        """
        with transaction.atomic():
            request = AllocationRequest.objects.select_for_update().get(pk=self.pk)
            group = request.request_group
            if group is None:
                return
            request.request_group = None
            request.save(update_fields=['request_group'])
            if failure is None:
                group.on_allocation_end()
            else:
                group.on_allocation_fail(failure)

    @override
    def __str__(self) -> str:
        return str(super().__str__()) + f', ALLOCATION_UNIT: {self.allocation_unit.id}'


class CleanupRequest(SandboxRequest):
    """Represents a request to clean up and delete a sandbox allocation unit."""

    stages: models.Manager['CleanupStage']
    stackcleanupstage: 'StackCleanupStage'
    networkingansiblecleanupstage: 'NetworkingAnsibleCleanupStage'
    useransiblecleanupstage: 'UserAnsibleCleanupStage'

    allocation_unit = models.OneToOneField(
        SandboxAllocationUnit,
        on_delete=models.CASCADE,
        related_name='cleanup_request',
    )
    delete_pool = models.BooleanField(
        default=False,
        help_text='Whether the pool is deleted once this cleanup removes its last unit.',
    )
    retry_count = models.PositiveSmallIntegerField(
        default=0,
        help_text='How many times the failed cleanup has been started again by itself.',
    )

    @property
    def is_finished(self) -> bool:
        """Whether all stages are finished."""
        return self.stages.filter(finished=False).count() == 0

    @property
    def is_settled(self) -> bool:
        """Whether all stages are finished; no other job outlives them."""
        return self.is_finished

    @property
    def is_failed(self) -> bool:
        """Whether all stages are finished."""
        return self.stages.filter(failed=True).count() > 0

    @override
    def __str__(self) -> str:
        return str(super().__str__()) + f', ALLOCATION_UNIT: {self.allocation_unit.id}'


class Stage(models.Model):
    """Abstract base class for stages."""

    start = models.DateTimeField(
        null=True, default=None, help_text='Timestamp indicating when the stage execution started.'
    )
    end = models.DateTimeField(
        null=True, default=None, help_text='Timestamp indicating when the stage execution ended.'
    )
    failed = models.BooleanField(
        default=False, help_text='Indicates whether the stage execution failed.'
    )
    error_message = models.TextField(
        null=True, default=None, help_text='Error message describing the potential error.'
    )
    finished = models.BooleanField(
        default=False, help_text='Indicates whether the stage execution has finished.'
    )
    stop_requested_at = models.DateTimeField(
        null=True,
        default=None,
        help_text='When a stop of the running stage job was first requested.',
    )
    job_requested_at = models.DateTimeField(
        null=True,
        default=None,
        help_text='When the stage was created for a job that is queued once its request commits.',
    )

    id: int

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for Stage model."""

        abstract = True
        ordering = ['id']

    @property
    def stop_pending(self) -> bool:
        """Whether a stop of the stage job was requested and its end is not confirmed yet."""
        return self.stop_requested_at is not None and not self.finished

    @property
    def within_enqueue_grace(self) -> bool:
        """
        Whether the job of the stage was requested so recently that it may still be queued.
        """
        return (
            self.job_requested_at is not None
            and timezone.now() - self.job_requested_at < ENQUEUE_GRACE
        )

    @property
    def enqueue_pending(self) -> bool:
        """
        Whether the job of the stage is yet to be queued: the stage has no job row, and its
        job was requested within the enqueue grace.
        """
        return getattr(self, 'rq_job', None) is None and self.within_enqueue_grace

    @override
    def __str__(self) -> str:
        return (
            f'ID: {self.id}, START: {self.start}, END: {self.end}, FAILED: {self.failed}, '
            f'ERROR: {self.error_message}'
        )


class AllocationStage(Stage):
    """Concrete stage associated with an allocation request."""

    rq_job: 'AllocationRQJob'
    terraform_outputs: models.Manager['AllocationTerraformOutput']
    outputs: models.Manager['AllocationAnsibleOutput']
    # Reverse OneToOne links of the multi-table-inheritance children.
    stackallocationstage: 'StackAllocationStage'
    networkingansibleallocationstage: 'NetworkingAnsibleAllocationStage'
    useransibleallocationstage: 'UserAnsibleAllocationStage'
    # Reverse OneToOne links of the concrete ExternalDependency subclasses.
    terraformstack: 'TerraformStack'
    systemprocess: 'SystemProcess'
    container: 'Container'

    allocation_request_fk_many = models.ForeignKey(
        AllocationRequest, on_delete=models.CASCADE, related_name='stages'
    )


class CleanupStage(Stage):
    """Concrete stage associated with a cleanup request."""

    rq_job: 'CleanupRQJob'
    terraform_outputs: models.Manager['CleanupTerraformOutput']
    outputs: models.Manager['CleanupAnsibleOutput']
    # Reverse OneToOne links of the multi-table-inheritance children.
    stackcleanupstage: 'StackCleanupStage'
    networkingansiblecleanupstage: 'NetworkingAnsibleCleanupStage'
    useransiblecleanupstage: 'UserAnsibleCleanupStage'
    # Reverse OneToOne link of the concrete ExternalDependencyCleanup subclass.
    containercleanup: 'ContainerCleanup'

    cleanup_request_fk_many = models.ForeignKey(
        CleanupRequest, on_delete=models.CASCADE, related_name='stages'
    )


class StackAllocationStage(AllocationStage):
    """Allocation stage tracking Terraform stack creation."""

    allocation_request = models.OneToOneField(
        AllocationRequest,
        on_delete=models.CASCADE,
    )
    status = models.CharField(null=True, max_length=30, help_text='Stack status')
    status_reason = models.TextField(null=True, help_text='Stack status reason')

    @override
    def __str__(self) -> str:
        return (
            str(super().__str__()) + f', REQUEST: {self.allocation_request}, STATUS: {self.status},'
            f' STATUS_REASON: {self.status_reason}'
        )


class StackCleanupStage(CleanupStage):
    """Cleanup stage tracking Terraform stack deletion."""

    cleanup_request = models.OneToOneField(
        CleanupRequest,
        on_delete=models.CASCADE,
    )

    @override
    def __str__(self) -> str:
        return str(super().__str__()) + f', REQUEST: {self.cleanup_request}'


class RQJob(models.Model):
    """Abstract base class for RQ job tracking models."""

    job_id = models.TextField()

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for RQJob model."""

        abstract = True
        ordering = ['pk']


class AllocationRQJob(RQJob):
    """Tracks the RQ job ID for an allocation stage."""

    allocation_stage = models.OneToOneField(
        AllocationStage,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='rq_job',
    )

    @override
    def __str__(self) -> str:
        return f'STAGE: {self.allocation_stage.id}, JOB_ID: {self.job_id}'


class CleanupRQJob(RQJob):
    """Tracks the RQ job ID for a cleanup stage."""

    cleanup_stage = models.OneToOneField(
        CleanupStage,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='rq_job',
    )

    @override
    def __str__(self) -> str:
        return f'STAGE: {self.cleanup_stage.id}, JOB_ID: {self.job_id}'


class ExternalDependency(models.Model):
    """Abstract base class for external allocation dependencies."""

    allocation_stage = models.OneToOneField(AllocationStage, on_delete=models.CASCADE)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for ExternalDependency model."""

        abstract = True
        ordering = ['id']


class ExternalDependencyCleanup(models.Model):
    """Abstract base class for external cleanup dependencies."""

    cleanup_stage = models.OneToOneField(CleanupStage, on_delete=models.CASCADE)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for ExternalDependencyCleanup model."""

        abstract = True
        ordering = ['id']


class TerraformOutput(StageOutputRow):
    """Abstract base class for storing Terraform process output lines."""

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for TerraformOutput model."""

        abstract = True


class AllocationTerraformOutput(TerraformOutput):
    """Stores Terraform output lines for an allocation stage."""

    allocation_stage = models.ForeignKey(
        AllocationStage, on_delete=models.CASCADE, related_name='terraform_outputs'
    )

    @override
    def __str__(self) -> str:
        return f'{str(super().__str__())} STAGE: {self.allocation_stage.id}'


class CleanupTerraformOutput(TerraformOutput):
    """Stores Terraform output lines for a cleanup stage."""

    cleanup_stage = models.ForeignKey(
        CleanupStage, on_delete=models.CASCADE, related_name='terraform_outputs'
    )

    @override
    def __str__(self) -> str:
        return f'{str(super().__str__())} STAGE: {self.cleanup_stage.id}'


class TerraformStack(ExternalDependency):
    """Tracks the Terraform stack process associated with an allocation stage."""

    stack_id = models.TextField()

    @override
    def __str__(self) -> str:
        return f'STAGE: {self.allocation_stage.id}, STACK: {self.stack_id}'


class SystemProcess(ExternalDependency):
    """Tracks a system process associated with an allocation stage."""

    process_id = models.TextField()

    @override
    def __str__(self) -> str:
        return f'STAGE: {self.allocation_stage.id}, PROCESS: {self.process_id}'


FLAT_ROLE = ''
"""The role value marking a sandbox's flat, role-free Netbird access row."""


class SandboxNetbirdAccess(models.Model):
    """
    Stores one Netbird "access" group and setup key created for a sandbox. Every
    sandbox has exactly one row with role='', the flat access group and setup key
    reachable by every player regardless of team; a sandbox whose pool declares
    roles additionally has one further row per declared role, each widening its
    own setup key to also name the flat group.

    All fields except role are nullable so that a partial-failure state can be
    persisted: the cleanup path tolerates nulls and issues 404-tolerant deletes.
    The row outlives its sandbox and goes with its allocation unit.
    """

    id: int

    # Implicit FK attributes; Sandbox has a CharField primary key.
    sandbox_id: str | None
    allocation_unit_id: int | None

    sandbox = models.ForeignKey(
        Sandbox,
        on_delete=models.SET_NULL,
        null=True,
        related_name='netbird_access',
        help_text='Sandbox these access resources belong to; null once it is deleted.',
    )
    allocation_unit = models.ForeignKey(
        SandboxAllocationUnit,
        on_delete=models.CASCADE,
        null=True,
        related_name='netbird_accesses',
        help_text='Allocation unit whose cleanup tears these access resources down.',
    )
    role = models.CharField(
        max_length=128,
        default=FLAT_ROLE,
        blank=True,
        help_text=(
            "Declared role this access group is scoped to; '' for the sandbox's shared access."
        ),
    )
    access_group_id = models.CharField(
        max_length=255,
        null=True,
        default=None,
        help_text='Netbird group ID shared by all client peers of this role.',
    )
    access_setup_key_id = models.CharField(
        max_length=255,
        null=True,
        default=None,
        help_text="Netbird setup key ID for this role's access group.",
    )
    access_setup_key_value = models.TextField(
        null=True,
        default=None,
        help_text="Plaintext setup key for this role's access group.",
    )
    dns_nameserver_group_id = models.CharField(
        max_length=255,
        null=True,
        default=None,
        help_text='Netbird DNS nameserver group ID distributed to the access group.',
    )

    class Meta:
        ordering = ['id']
        unique_together = [('sandbox', 'role')]
        verbose_name_plural = 'sandbox netbird access'


class SandboxNetbirdResources(models.Model):
    """
    Stores IDs of Netbird control-plane objects created for one VPN entrypoint
    within a sandbox. One row per entrypoint.

    The routes only decide which clients are offered them (they are distributed
    to the access groups the entrypoint is sourced from); reach is granted by the
    policy, and without it NetBird gives the routes to no client. Provisioning
    therefore creates the policy before the routes.

    All fields are nullable so that a partial-failure state can be persisted:
    the cleanup path tolerates nulls and issues 404-tolerant deletes. The row
    outlives its sandbox and goes with its allocation unit.
    """

    # Implicit FK attributes; Sandbox has a CharField primary key.
    sandbox_id: str | None
    allocation_unit_id: int | None

    sandbox = models.ForeignKey(
        Sandbox,
        on_delete=models.SET_NULL,
        null=True,
        related_name='netbird_resources',
        help_text='Sandbox these resources belong to; null once it is deleted.',
    )
    allocation_unit = models.ForeignKey(
        SandboxAllocationUnit,
        on_delete=models.CASCADE,
        null=True,
        related_name='netbird_resources',
        help_text='Allocation unit whose cleanup tears these resources down.',
    )
    entrypoint_host_name = models.CharField(
        max_length=255,
        help_text='Name of the topology host configured as VPN entrypoint.',
    )
    host_group_id = models.CharField(
        max_length=255,
        null=True,
        default=None,
        help_text='Netbird group ID for the entrypoint peer.',
    )
    host_setup_key_id = models.CharField(
        max_length=255,
        null=True,
        default=None,
        help_text='Netbird setup key ID used by the agent on the entrypoint VM.',
    )
    host_setup_key_value = models.TextField(
        null=True,
        default=None,
        help_text='Plaintext setup key for the entrypoint host agent.',
    )
    route_ids = models.TextField(
        null=True,
        default=None,
        help_text='Comma-separated Netbird route IDs for this entrypoint.',
    )
    route_cidrs = models.TextField(
        null=True,
        default=None,
        help_text='Comma-separated CIDR strings for the routes of this entrypoint.',
    )
    policy_id = models.CharField(
        max_length=255,
        null=True,
        default=None,
        help_text='Netbird access policy ID permitting client-to-host traffic.',
    )
    source_roles = models.TextField(
        null=True,
        default=None,
        help_text=(
            "Comma-separated declared roles this entrypoint's policy and routes source "
            'from; empty for the flat role or for no role. Null on a row created before '
            'this field existed.'
        ),
    )

    class Meta:
        ordering = ['id']
        unique_together = [('sandbox', 'entrypoint_host_name')]
        verbose_name_plural = 'sandbox netbird resources'

    def get_route_id_list(self) -> list[str]:
        if not self.route_ids:
            return []
        return [r for r in self.route_ids.split(',') if r]

    def set_route_id_list(self, ids: list[str]) -> None:
        self.route_ids = ','.join(ids)

    def get_route_cidr_list(self) -> list[str]:
        if not self.route_cidrs:
            return []
        return [r for r in self.route_cidrs.split(',') if r]

    def set_route_cidr_list(self, cidrs: list[str]) -> None:
        self.route_cidrs = ','.join(cidrs)

    def get_source_role_list(self) -> list[str] | None:
        """Return the declared roles sourcing this entrypoint, None on a pre-field row."""
        if self.source_roles is None:
            return None
        return [r for r in self.source_roles.split(',') if r]

    def set_source_role_list(self, roles: list[str]) -> None:
        self.source_roles = ','.join(role for role in roles if role)


class SandboxNetbirdEphemeralKey(models.Model):
    """
    Stores one Netbird setup key minted for a multi-role requester's exact
    combination of held roles, so a repeat request for that same combination
    reuses it instead of minting (and eventually orphaning) another.

    The row outlives its sandbox and goes with its allocation unit, whose cleanup
    deletes the key before the access groups it names.
    """

    # Implicit FK attributes; Sandbox has a CharField primary key.
    sandbox_id: str | None
    allocation_unit_id: int | None

    sandbox = models.ForeignKey(
        Sandbox,
        on_delete=models.SET_NULL,
        null=True,
        related_name='netbird_ephemeral_keys',
        help_text='Sandbox this key was minted for; null once it is deleted.',
    )
    allocation_unit = models.ForeignKey(
        SandboxAllocationUnit,
        on_delete=models.CASCADE,
        null=True,
        related_name='netbird_ephemeral_keys',
        help_text='Allocation unit whose cleanup deletes this key.',
    )
    role_set = models.TextField(
        help_text='Sorted, comma-separated roles this key spans, identifying the combination.',
    )
    key_id = models.CharField(max_length=255, help_text='Netbird setup key ID.')
    key_value = models.TextField(help_text='Plaintext setup key value.')

    class Meta:
        unique_together = [('sandbox', 'role_set')]
        verbose_name_plural = 'sandbox netbird ephemeral keys'


class SandboxRequestGroup(OrderedByIdModel):
    """
    Represents allocation/cleanup requests created at the same time.

    Keeps track of the request progress and sends email notifications.
    """

    pool = models.ForeignKey(Pool, on_delete=models.CASCADE)
    unit_count = models.IntegerField()
    email = models.EmailField()
    failed_count = models.IntegerField(default=0)
    finished_count = models.IntegerField(default=0)

    def on_allocation_fail(self, exc: Exception) -> None:
        """Handle a sandbox allocation failure: track counts and send notification if first fail."""
        with transaction.atomic():
            self._refresh_locked()
            if self.failed_count == 0:
                transaction.on_commit(partial(self._send_fail_notification, exc=exc))
                LOG.debug('Sandbox allocation Failure email notification sent.')
            self.failed_count += 1
            self.finished_count += 1
            self.save()
            self._send_summary_once_all_finished()

    def on_allocation_end(self) -> None:
        """Handle a sandbox allocation completion: track counts and send summary if all done."""
        with transaction.atomic():
            self._refresh_locked()
            self.finished_count += 1
            self.save()
            self._send_summary_once_all_finished()

    def on_allocation_removed(self) -> None:
        """
        Handle a unit removed before its allocation started: it no longer counts in the group.

        The group is deleted once no unit is left, else the summary is sent when every
        remaining unit has finished.
        """
        with transaction.atomic():
            self._refresh_locked()
            self.unit_count -= 1
            if self.unit_count <= 0:
                self.delete()
                return
            self.save()
            self._send_summary_once_all_finished()

    def _refresh_locked(self) -> None:
        """Reload the counts under the row lock, held until the transaction ends."""
        self.refresh_from_db(from_queryset=SandboxRequestGroup.objects.select_for_update())

    def _send_summary_once_all_finished(self) -> None:
        if self.finished_count == self.unit_count:
            transaction.on_commit(self._send_summary_notification)
            LOG.debug('Sandbox allocation End email notification sent.')

    def _send_fail_notification(self, exc: Exception) -> None:
        body = f"""
        Something went wrong during sandbox allocation in Pool {self.pool.id}.

        Error detail: {str(exc)}
        """
        send_email(
            self.email,
            f'CRCZP Pool {self.pool.id} - FAILED sandbox allocation',
            body,
            settings.CRCZP_CONFIG,
        )

    def _send_summary_notification(self) -> None:
        try:
            body = f"""
            All allocations you created in Pool {self.pool.id} have finished.

            Successful - {self.finished_count - self.failed_count}
            Failed - {self.failed_count}
            """
            send_email(
                self.email,
                f'CRCZP Pool {self.pool.id} - FINAL sandbox allocations report',
                body,
                settings.CRCZP_CONFIG,
            )
        finally:
            self.delete()


class PoolRoleGrant(models.Model):
    """Represents one role held by one user-and-group service user in one pool."""

    id: int
    pool_id: int

    pool = models.ForeignKey(Pool, on_delete=models.CASCADE, related_name='role_grants')
    user = models.IntegerField(help_text='User id from the user-and-group service.')
    role = models.CharField(max_length=128, db_index=True)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta options for PoolRoleGrant model."""

        unique_together = [('pool', 'user', 'role')]

    @override
    def __str__(self) -> str:
        return f'POOL: {self.pool_id}, USER: {self.user}, ROLE: {self.role}'
