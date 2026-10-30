"""Handlers for sandbox allocation and cleanup request lifecycle management."""

import abc
from collections.abc import Callable, Sequence
from datetime import timedelta
from functools import partial
from typing import Any, override
from uuid import uuid4

import django_rq
import structlog
from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from rq import Queue
from rq.exceptions import AbandonedJobError
from rq.job import Job

from crczp.sandbox_ansible_app.models import (
    AnsibleAllocationStage,
    NetworkingAnsibleAllocationStage,
    NetworkingAnsibleCleanupStage,
    UserAnsibleAllocationStage,
    UserAnsibleCleanupStage,
)
from crczp.sandbox_common_lib import exceptions, utils
from crczp.sandbox_instance_app.lib import netbird, sandboxes
from crczp.sandbox_instance_app.lib.stage_cancellation import (
    STOP_CONFIRMATION_SECONDS,
    cancel_stages,
)
from crczp.sandbox_instance_app.lib.stage_handlers import (
    AllocationAnsibleStageHandler,
    AllocationStackStageHandler,
    AnsibleStageHandler,
    CancellableJob,
    CleanupAnsibleStageHandler,
    CleanupStackStageHandler,
    NetbirdProvisioningHandler,
    StackStageHandler,
    StageHandler,
)
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    AllocationRQJob,
    CleanupRequest,
    CleanupRQJob,
    CleanupStage,
    Sandbox,
    SandboxAllocationUnit,
    SandboxRequest,
    SandboxRequestGroup,
    StackAllocationStage,
    StackCleanupStage,
)

LOG = structlog.get_logger()

OPENSTACK_QUEUE = 'openstack'
ANSIBLE_QUEUE = 'ansible'
AllocationStage = StackAllocationStage | AnsibleAllocationStage


class RequestHandler(abc.ABC):
    """
    Handles DB SandboxRequest object and generalizes its common tasks.
    """

    queue_default: Queue = django_rq.get_queue()
    queue_stack: Queue = django_rq.get_queue(
        OPENSTACK_QUEUE, default_timeout=settings.CRCZP_CONFIG.sandbox_build_timeout
    )
    queue_ansible: Queue = django_rq.get_queue(
        ANSIBLE_QUEUE, default_timeout=settings.CRCZP_CONFIG.sandbox_ansible_timeout
    )
    request: SandboxRequest | None = None

    @abc.abstractmethod
    def enqueue_request(self, *args: Any, **kwargs: Any) -> None:
        """
        Handles request stages creation and their enqueuing.
        """

    def cancel_request(
        self,
        request: AllocationRequest | CleanupRequest,
        wait_seconds: float = STOP_CONFIRMATION_SECONDS,
    ) -> bool:
        """
        Cancel all unfinished stages of the request, waiting a bounded time for their jobs to end.

        :returns: Whether the stop of a stage is still pending.
        :raises ValidationError: The request is finished.
        """
        return bool(cancel_stages(self.get_stage_handlers_to_cancel(request), wait_seconds))

    def get_stage_handlers_to_cancel(
        self, request: AllocationRequest | CleanupRequest
    ) -> list[CancellableJob]:
        """
        Get handlers of the jobs to cancel for a request that has not settled: its stages, and
        any other job the request still has running.

        :raises ValidationError: The request has no stages yet, or has nothing left to cancel.
        """
        self.request = request
        if not request.stages.exists():
            raise exceptions.ValidationError(
                f'Request {request.__class__.__name__} with ID {request.id} has no stages yet,'
                f' its jobs are still being queued. Retry shortly.'
            )
        if request.is_settled:
            raise exceptions.ValidationError(
                f'Request {request.__class__.__name__} with ID {request.id}'
                f' is finished and does not need cancelling.'
            )
        return self._get_cancellable_handlers()

    def _get_cancellable_handlers(self) -> list[CancellableJob]:
        """
        Get the handlers of every job of the request that cancelling may have to stop.
        """
        return list(self._get_stage_handlers())

    @abc.abstractmethod
    def _create_stage_handlers(self, *args: Any, **kwargs: Any) -> list[StageHandler]:
        """
        Create a new DB stages for this request and return their handlers.
        """

    @abc.abstractmethod
    def _get_stage_handlers(self) -> list[StageHandler]:
        """
        Get handlers of existing DB stages for this request.
        """

    @staticmethod
    def _store_stage_job_ids(stage_handlers: list[StageHandler], stage_job_ids: list[str]) -> None:
        """Store the job ID of each stage handler, given in the same order as its job."""
        for stage_handler, stage_job_id in zip(stage_handlers, stage_job_ids, strict=True):
            stage_handler.set_job_id(stage_job_id)

    def _enqueue_stage_jobs(
        self,
        stage_handlers: list[StageHandler],
        stage_job_ids: list[str],
        netbird_job_id: str | None = None,
        on_failure: Callable[..., None] | None = None,
    ) -> None:
        """
        Enqueue the methods of the given stage handlers under the given job IDs, each job
        depending on the previous one.

        The order of stage handlers is important.
          Every subsequent stage depends on the previous one, and it is enqueued as such.

        ``netbird_job_id`` is an optional extra job that the user-ansible stage must
        additionally wait for: the NetBird provisioning job, which writes the setup key that
        the user-ansible inventory consumes. Stage sets without a user-ansible allocation
        stage (e.g. cleanup) never match, so the parameter is harmless there.

        ``on_failure`` is the RQ failure callback of every stage job.
        """
        previous_job_id = None
        for stage_handler, job_id in zip(stage_handlers, stage_job_ids, strict=True):
            if isinstance(stage_handler, StackStageHandler):
                queue = self.queue_stack
            elif isinstance(stage_handler, AnsibleStageHandler):
                queue = self.queue_ansible
            else:
                queue = self.queue_default
            depends_on: str | list[str] | None = previous_job_id
            if netbird_job_id is not None and isinstance(
                stage_handler.stage, UserAnsibleAllocationStage
            ):
                depends_on = [
                    dependency
                    for dependency in (previous_job_id, netbird_job_id)
                    if dependency is not None
                ]
            queue.enqueue(
                stage_handler.execute, job_id=job_id, depends_on=depends_on, on_failure=on_failure
            )
            previous_job_id = job_id

    @staticmethod
    def create_request_group(units: list[SandboxAllocationUnit]) -> SandboxRequestGroup:
        """Create a SandboxRequestGroup for tracking batch allocation progress."""
        email = units[0].created_by.email if units[0].created_by else ''
        group = SandboxRequestGroup.objects.create(
            pool=units[0].pool, unit_count=len(units), email=email
        )
        group.save()
        return group


class AllocationRequestHandler(RequestHandler):
    """
    Specifies allocation request stages and tasks for their manipulation.
    """

    request: AllocationRequest

    @transaction.atomic
    def _enqueue_stages(self, sandbox: Sandbox, stage_handlers: list[StageHandler]) -> None:
        """
        Handles request stages creation (or restart) and their enqueuing.
        """
        transaction.on_commit(
            partial(self._enqueue_allocation_request, sandbox, stage_handlers, self.request)
        )

    @override
    def _get_cancellable_handlers(self) -> list[CancellableJob]:
        """
        Get the handlers of the stages of the allocation request, and of its NetBird
        provisioning job when one is tracked or may be yet to be queued.
        """
        handlers = super()._get_cancellable_handlers()
        netbird_handler = NetbirdProvisioningHandler(self.request)
        if not netbird_handler.finished:
            handlers.append(netbird_handler)
        return handlers

    def _enqueue_allocation_request(
        self,
        sandbox: Sandbox,
        stage_handlers: list[StageHandler],
        allocation_request: AllocationRequest,
    ) -> None:
        """
        Enqueue the NetBird provisioning job (when configured) and the stage chain, whose last
        stage marks the sandbox ready.

        The job IDs are chosen first and stored on the stages and on the allocation request in
        one transaction, before any job is queued. A job that starts at once, or a Cancel, thus
        finds every ID, and a stage whose job was never queued reads as lost.

        Runs from the post-commit hook so the committed Sandbox row is visible to
        the provisioning worker. NetBird provisioning is a SEPARATE job on the
        ansible queue, never inline on the default queue, so a slow NetBird
        endpoint can no longer block this orchestration job and starve other
        pools' allocation requests. The user-ansible stage depends on the job so
        the setup key exists before that stage runs. Per-role SSH keypairs are instead
        generated inside the tracked networking stage (stage_handlers.py), which
        already needs the same definition fetch to build its own inventory.
        """
        netbird_job_id = str(uuid4()) if netbird.is_netbird_configured() else None
        stage_job_ids = [str(uuid4()) for _ in stage_handlers]
        with transaction.atomic():
            self._store_stage_job_ids(stage_handlers, stage_job_ids)
            AllocationRequest.objects.filter(pk=allocation_request.pk).update(
                netbird_job_id=netbird_job_id
            )
        allocation_request.netbird_job_id = netbird_job_id
        if netbird_job_id is not None:
            self._enqueue_netbird_provision(sandbox, netbird_job_id)
        self._enqueue_stage_jobs(
            stage_handlers,
            stage_job_ids,
            netbird_job_id=netbird_job_id,
            on_failure=fail_abandoned_allocation_job,
        )

    def _enqueue_netbird_provision(self, sandbox: Sandbox, job_id: str) -> None:
        """Enqueue NetBird provisioning for the sandbox under the given job ID.

        Whether NetBird is configured is decided by the caller from the cheap in-memory
        config check: the per-sandbox "has VPN entrypoints?" decision needs a git
        definition fetch and stays inside the job, off the default queue. When NetBird is
        not configured no job is enqueued and no dependency is added, so non-NetBird
        deployments are unaffected.
        """
        self.queue_ansible.enqueue(
            netbird.provision_netbird_for_sandbox,
            sandbox,
            job_id=job_id,
            on_failure=fail_abandoned_allocation_job,
        )

    def _create_linked_request_group(
        self, units: list[SandboxAllocationUnit]
    ) -> SandboxRequestGroup | None:
        """
        Create the request group of the units that still exist and link their pre-created
        allocation requests to it, all under the units' row locks.

        A unit removed later leaves the group as it is removed; a unit already removed is
        not counted.

        :returns: The group, or None when no unit is left.
        """
        with transaction.atomic():
            existing_units = list(
                SandboxAllocationUnit.objects.select_for_update(of=('self',)).filter(
                    pk__in=[unit.id for unit in units]
                )
            )
            if not existing_units:
                return None
            group = self.create_request_group(existing_units)
            AllocationRequest.objects.filter(allocation_unit__in=existing_units).update(
                request_group=group
            )
            return group

    def _create_allocation_jobs(
        self, units: list[SandboxAllocationUnit], created_by: User | None
    ) -> None:
        """Create allocation requests and enqueue stage jobs for each unit.

        Runs on the ``default`` queue and is DB-only + enqueue (no external I/O),
        so it returns quickly and never starves other pools' allocation jobs
        waiting on the same queue. NetBird provisioning is dispatched as a
        separate job from _enqueue_stages, off the default queue.
        """
        if units[0].pool.send_emails and created_by is not None and created_by.email:
            allocation_group = self._create_linked_request_group(units)
        else:
            allocation_group = None

        for unit in units:
            with transaction.atomic():
                # Locked so that Cancel all allocations and this job do not
                # interleave; it may have removed the unit while this job waited in the queue.
                if (
                    SandboxAllocationUnit.objects.select_for_update().filter(pk=unit.id).first()
                    is None
                ):
                    LOG.info('allocation_unit_removed_before_build', unit_id=unit.id)
                    continue
                LOG.info('Creating sandbox for allocation unit: %s', unit.id)
                # The AllocationRequest is normally pre-created synchronously in the
                # request thread (see requests.create_allocations_requests) so the
                # listing endpoint never serves a unit with allocation_request=null;
                # reuse it here. get_or_create keeps callers that enqueue without
                # pre-creating the request (e.g. tests, restart) working unchanged.
                self.request, _ = AllocationRequest.objects.get_or_create(allocation_unit=unit)
                if allocation_group is not None:
                    self.request.request_group = allocation_group
                    self.request.save(update_fields=['request_group'])
                pri_key, pub_key = utils.generate_ssh_keypair()
                sandbox = Sandbox(
                    id=sandboxes.generate_new_sandbox_uuid(),
                    allocation_unit=unit,
                    private_user_key=pri_key,
                    public_user_key=pub_key,
                )
                sandbox.save()
                stage_handlers = self._create_stage_handlers(sandbox)
                self._enqueue_stages(sandbox, stage_handlers)

    def fail_abandoned_job(self, request: AllocationRequest, job_id: str) -> None:
        """
        Fail the allocation whose job was abandoned by a dead worker, and queue its cleanup.

        The abandoned stage fails with the reason that its worker died. Every other unfinished
          stage is cancelled, which deletes the jobs still deferred behind it, among them the
          user stage that also waits for the NetBird job. A NetBird job that was abandoned is
          forgotten. The cleanup is not queued while a stop of a stage is still pending.
        """
        self.request = request
        for handler in self._get_stage_handlers():
            if handler.job_id == job_id:
                handler.mark_abandoned()
        if request.netbird_job_id == job_id:
            NetbirdProvisioningHandler(request).mark_cancelled()
        self._cancel_and_queue_cleanup(request)

    def fail_lost_stage(
        self,
        request: AllocationRequest,
        stage_id: int,
        reason: str,
        *,
        stop_never_landed: bool = False,
    ) -> None:
        """
        Fail the allocation stage whose job is lost, then cancel the rest of the allocation and
        queue its cleanup, as for an abandoned job.

        Nothing is done when the stage finished or a person requested its stop since it was
        found lost; that stop and the stage's own end take over.

        :param stage_id: The ID of the stage that fails.
        :param reason: The error message the stage keeps.
        :param stop_never_landed: The stage was found lost with a stop requested long before,
            which its dead job never carried out; the stage is failed in spite of that stop.
        """
        self.request = request
        marked = False
        for handler in self._get_stage_handlers():
            if handler.stage.pk == stage_id:
                marked = handler.mark_abandoned(reason, unless_stop_requested=not stop_never_landed)
        if not marked:
            LOG.info('Lost stage left alone', request=request.id, stage=stage_id)
            return
        self._cancel_and_queue_cleanup(request)

    def _cancel_and_queue_cleanup(self, request: AllocationRequest) -> None:
        """
        Cancel the unfinished stages of the failed allocation, then queue the cleanup of its
        unit unless a stop is still pending.
        """
        try:
            stop_pending = self.cancel_request(request, wait_seconds=0)
        except exceptions.ValidationError:
            stop_pending = False
        if stop_pending:
            LOG.warning('Cleanup of the abandoned allocation waits for a stop', request=request.id)
            return
        # requests imports this module.
        from crczp.sandbox_instance_app.lib import (
            requests,  # pylint: disable=import-outside-toplevel
        )

        try:
            requests.create_cleanup_request_force(request.allocation_unit, delete_pool=False)
        except exceptions.ValidationError as exc:
            LOG.warning('Cleanup of the abandoned allocation not queued', error=str(exc))

    @override
    def enqueue_request(  # pylint: disable=arguments-differ
        self, units: list[SandboxAllocationUnit], created_by: User | None
    ) -> None:
        """Enqueue the allocation request creation job for the given units."""
        self.queue_default.enqueue(self._create_allocation_jobs, units, created_by)

    def _create_restart_jobs(self, unit: SandboxAllocationUnit) -> None:
        LOG.info('Restarting sandbox allocation unit: %s', unit.id)
        self.request = unit.allocation_request
        self._ensure_restartable()
        old_sandbox = Sandbox.objects.get(allocation_unit=unit)
        # The unit's NetBird resources go before the new sandbox is provisioned.
        netbird.destroy_netbird_for_unit(unit)
        old_sandbox.delete()
        pri_key, pub_key = utils.generate_ssh_keypair()
        sandbox = Sandbox(
            id=sandboxes.generate_new_sandbox_uuid(),
            allocation_unit=unit,
            private_user_key=pri_key,
            public_user_key=pub_key,
        )
        sandbox.save()
        stage_handlers = self._restart_stage_handlers(sandbox)
        self._enqueue_stages(sandbox, stage_handlers)

    def restart_request(self, unit: SandboxAllocationUnit) -> None:
        """Enqueue a restart job for the given allocation unit."""
        self.queue_default.enqueue(self._create_restart_jobs, unit)

    def _create_db_stage[StageT: AllocationStage](
        self, stage_class: type[StageT], *args: Any, **kwargs: Any
    ) -> StageT:
        """
        Simplifies stage creation in database.
        """
        # Manager.create() is declared to return Self@Model, so ty cannot see that
        # `type[StageT].objects.create()` yields a StageT.
        return stage_class.objects.create(  # ty: ignore[invalid-return-type]
            *args,
            allocation_request=self.request,
            allocation_request_fk_many=self.request,
            job_requested_at=timezone.now(),
            **kwargs,
        )

    @override
    def _create_stage_handlers(self, sandbox: Sandbox) -> list[StageHandler]:  # pylint: disable=arguments-differ
        """
        Create a new DB stages for this request and return their handlers.
        """
        stack_stage = self._create_db_stage(StackAllocationStage)
        stack_stage_handler = AllocationStackStageHandler(stack_stage)

        networking_stage = self._create_db_stage(
            NetworkingAnsibleAllocationStage,
            repo_url=settings.CRCZP_CONFIG.ansible_networking_url,
            rev=settings.CRCZP_CONFIG.ansible_networking_rev,
        )
        networking_stage_handler = AllocationAnsibleStageHandler(networking_stage, sandbox)

        user_stage = self._create_db_stage(
            UserAnsibleAllocationStage,
            repo_url=self.request.allocation_unit.pool.definition.url,
            rev=self.request.allocation_unit.pool.rev_sha,
        )
        user_stage_handler = AllocationAnsibleStageHandler(user_stage, sandbox)

        return [stack_stage_handler, networking_stage_handler, user_stage_handler]

    def _ensure_restartable(self) -> None:
        """
        Refuse a restart of an allocation that is still in progress or whose user stage did not
        fail.

        :raises ValidationError: When the allocation cannot be restarted.
        """
        if not self.request.is_finished:
            raise exceptions.ValidationError('Allocation of the sandbox is still in progress.')
        if not self.request.useransibleallocationstage.failed:
            raise exceptions.ValidationError(
                'All stages finished without failing. Only failed stages can be restarted.'
            )

    def _restart_stage_handlers(self, sandbox: Sandbox) -> list[StageHandler]:
        """
        Restart failed DB stages for this request and return their handlers.
        """
        self._ensure_restartable()

        stage_handlers: list[StageHandler] = []
        if self.request.stackallocationstage.failed:
            self.request.stackallocationstage.delete()
            stack_stage = self._create_db_stage(StackAllocationStage)
            stage_handlers.append(AllocationStackStageHandler(stack_stage))

        if (
            self.request.stackallocationstage.failed
            or self.request.networkingansibleallocationstage.failed
        ):
            self.request.networkingansibleallocationstage.delete()
            networking_stage = self._create_db_stage(
                NetworkingAnsibleAllocationStage,
                repo_url=settings.CRCZP_CONFIG.ansible_networking_url,
                rev=settings.CRCZP_CONFIG.ansible_networking_rev,
            )
            stage_handlers.append(AllocationAnsibleStageHandler(networking_stage, sandbox))

        self.request.useransibleallocationstage.delete()
        user_stage = self._create_db_stage(
            UserAnsibleAllocationStage,
            repo_url=self.request.allocation_unit.pool.definition.url,
            rev=self.request.allocation_unit.pool.rev_sha,
        )
        stage_handlers.append(AllocationAnsibleStageHandler(user_stage, sandbox))

        return stage_handlers

    @override
    def _get_stage_handlers(self) -> list[StageHandler]:
        """
        Get handlers of existing DB stages for this request.
        """
        user_handler = AllocationAnsibleStageHandler(self.request.useransibleallocationstage)
        networking_handler = AllocationAnsibleStageHandler(
            self.request.networkingansibleallocationstage
        )
        stack_handler = AllocationStackStageHandler(self.request.stackallocationstage)

        return [user_handler, networking_handler, stack_handler]


class CleanupRequestHandler(RequestHandler):
    """
    Specifies cleanup request stages and tasks for their manipulation.
    """

    request: CleanupRequest
    delete_pool: bool

    def __init__(self, delete_pool: bool = False) -> None:
        self.delete_pool = delete_pool

    @transaction.atomic
    def _enqueue_stages(self) -> None:
        """
        Handles request stages creation and their enqueuing.
        """
        stage_handlers = self._create_stage_handlers()
        transaction.on_commit(partial(self._enqueue_cleanup_request, stage_handlers))

    def _enqueue_cleanup_request(self, stage_handlers: list[StageHandler]) -> None:
        """
        Store the job ID of each stage in one transaction, then enqueue the stage chain, whose
        last stage removes the allocation unit, under those IDs.
        """
        stage_job_ids = [str(uuid4()) for _ in stage_handlers]
        with transaction.atomic():
            self._store_stage_job_ids(stage_handlers, stage_job_ids)
        self._enqueue_stage_jobs(stage_handlers, stage_job_ids)

    def _create_cleanup_jobs(self, unit: SandboxAllocationUnit) -> None:
        """
        Create the stages of the unit's cleanup request and queue their jobs.

        The request is read from the database; it is created only when it was removed after
        the job was queued.
        """
        self.request, created = CleanupRequest.objects.get_or_create(allocation_unit_id=unit.id)
        if created:
            LOG.info('Created cleanup request %s for allocation unit %s', self.request.id, unit.id)
        else:
            LOG.info('Reusing cleanup request %s for allocation unit %s', self.request.id, unit.id)

        self._enqueue_stages()

    @override
    def enqueue_request(self, unit: SandboxAllocationUnit) -> None:  # pylint: disable=arguments-differ
        """
        Create the unit's cleanup request, or restart its existing one without stages, and
        queue the creation of its stages once the transaction commits.

        A reused request gets the current time as its creation time, from which a missing
        creation job counts as lost, and starts counting its automatic retries anew.
        """
        with transaction.atomic():
            cleanup_request, created = CleanupRequest.objects.get_or_create(
                allocation_unit=unit, defaults={'delete_pool': self.delete_pool}
            )
            if not created:
                cleanup_request.stages.all().delete()
                cleanup_request.created = timezone.now()
                cleanup_request.delete_pool = self.delete_pool
                cleanup_request.retry_count = 0
                cleanup_request.save(update_fields=['created', 'delete_pool', 'retry_count'])
            transaction.on_commit(
                partial(self.queue_default.enqueue, self._create_cleanup_jobs, unit)
            )

    def retry_request(self, request: CleanupRequest) -> bool:
        """
        Queue another run of the failed cleanup, after the configured delay, unless it has
        been retried as often as the configured limit allows.

        :returns: Whether another run was queued.
        """
        with transaction.atomic():
            locked_request = (
                CleanupRequest.objects.select_for_update().filter(pk=request.pk).first()
            )
            if (
                locked_request is None
                or locked_request.retry_count >= settings.CRCZP_CONFIG.cleanup_retry_limit
            ):
                return False
            locked_request.retry_count += 1
            locked_request.save(update_fields=['retry_count'])
        delay = settings.CRCZP_CONFIG.cleanup_retry_delay
        LOG.warning(
            'Retrying the failed cleanup',
            cleanup_request=request.id,
            retry=locked_request.retry_count,
            delay_seconds=delay,
        )
        if delay > 0:
            self.queue_default.enqueue_in(
                timedelta(seconds=delay),
                self._rerun_failed_cleanup,
                request.allocation_unit.pk,
                locked_request.retry_count,
            )
        else:
            self.queue_default.enqueue(
                self._rerun_failed_cleanup, request.allocation_unit.pk, locked_request.retry_count
            )
        return True

    def fail_lost_stage(self, request: CleanupRequest, stage_id: int, reason: str) -> None:
        """
        Fail the cleanup stage whose job is lost, cancel the rest of the cleanup, and retry it,
        as for a failing stage.

        Nothing is done when the stage finished or a person requested its stop since it was
        found lost.

        :param stage_id: The ID of the stage that fails.
        :param reason: The error message the stage keeps.
        """
        self.request = request
        marked = False
        for handler in self._get_stage_handlers():
            if handler.stage.pk == stage_id:
                marked = handler.mark_abandoned(reason, unless_stop_requested=True)
        if not marked:
            LOG.info('Lost stage left alone', request=request.id, stage=stage_id)
            return
        try:
            self.cancel_request(request, wait_seconds=0)
        except exceptions.ValidationError:
            LOG.info('Request already finished, nothing to cancel', request=request.id)
        self.retry_request(request)

    def fail_lost_creation(self, request: CleanupRequest, reason: str) -> None:
        """
        Fail a cleanup whose stage-creation job is lost, then retry it.

        The stages are created in their failed state, so that the cleanup reads as a failed one
        that starts again by itself, up to the retry limit, and stays visible past it.

        Nothing is done when the request got stages, or was removed, since it was found lost.

        :param reason: The error message every stage keeps.
        """
        self.request = request
        with transaction.atomic():
            locked_request = (
                CleanupRequest.objects.select_for_update().filter(pk=request.pk).first()
            )
            if locked_request is None or locked_request.stages.exists():
                LOG.info('Lost cleanup creation left alone', request=request.id)
                return
            for handler in self._create_stage_handlers():
                handler.mark_abandoned(reason)
        self.retry_request(request)

    def _rerun_failed_cleanup(self, unit_id: int, retry_count: int) -> None:
        """
        Create the stages of a failed cleanup again and queue their jobs.

        Nothing is done when the cleanup was started anew, removed or completed in the
        meantime, so that a retry never doubles another run.

        :param unit_id: The allocation unit whose cleanup failed.
        :param retry_count: The retry that queued this run.
        """
        request = CleanupRequest.objects.filter(
            allocation_unit_id=unit_id, retry_count=retry_count
        ).first()
        if request is None or not (request.is_finished and request.is_failed):
            LOG.info('Retry of the cleanup skipped', allocation_unit_id=unit_id)
            return
        self._create_cleanup_jobs(request.allocation_unit)

    def _create_db_stage[StageT: CleanupStage](
        self, stage_class: type[StageT], *args: Any, **kwargs: Any
    ) -> StageT:
        """
        Simplifies stage creation in database.
        """
        return stage_class.objects.create(
            *args,
            cleanup_request=self.request,
            cleanup_request_fk_many=self.request,
            job_requested_at=timezone.now(),
            **kwargs,
        )

    @override
    def _create_stage_handlers(self) -> list[StageHandler]:  # pylint: disable=arguments-differ
        """
        Create a new DB stages for this request and return their handlers.
        """

        if hasattr(self.request, 'useransiblecleanupstage'):
            self.request.useransiblecleanupstage.delete()
        user_stage = self._create_db_stage(UserAnsibleCleanupStage)
        user_stage_handler = CleanupAnsibleStageHandler(user_stage)

        if hasattr(self.request, 'networkingansiblecleanupstage'):
            self.request.networkingansiblecleanupstage.delete()
        networking_stage = self._create_db_stage(NetworkingAnsibleCleanupStage)
        networking_stage_handler = CleanupAnsibleStageHandler(networking_stage)

        if hasattr(self.request, 'stackcleanupstage'):
            self.request.stackcleanupstage.delete()
        stack_stage = self._create_db_stage(StackCleanupStage)
        stack_stage_handler = CleanupStackStageHandler(stack_stage)

        return [user_stage_handler, networking_stage_handler, stack_stage_handler]

    @override
    def _get_stage_handlers(self) -> list[StageHandler]:
        """
        Get handlers of existing DB stages for this request.
        """
        stack_handler = CleanupStackStageHandler(self.request.stackcleanupstage)
        networking_handler = CleanupAnsibleStageHandler(self.request.networkingansiblecleanupstage)
        user_handler = CleanupAnsibleStageHandler(self.request.useransiblecleanupstage)
        return [stack_handler, networking_handler, user_handler]


def cancel_requests(
    requests_to_cancel: Sequence[AllocationRequest | CleanupRequest],
    wait_seconds: float = STOP_CONFIRMATION_SECONDS,
) -> list[bool | None]:
    """
    Cancel many requests, sending every stop first and waiting once for all of them.

    :param requests_to_cancel: Allocation and cleanup requests.
    :param wait_seconds: How long to wait for all their jobs to end, in seconds.
    :returns: Per request in the order given: whether the stop of one of its jobs is still
        pending, or None when the request was finished and nothing was cancelled.
    """
    handlers_per_request: list[list[CancellableJob] | None] = []
    for request in requests_to_cancel:
        request_handler = (
            AllocationRequestHandler()
            if isinstance(request, AllocationRequest)
            else CleanupRequestHandler()
        )
        try:
            handlers_per_request.append(request_handler.get_stage_handlers_to_cancel(request))
        except exceptions.ValidationError:
            handlers_per_request.append(None)
    pending = set(
        cancel_stages(
            [handler for handlers in handlers_per_request for handler in handlers or []],
            wait_seconds,
        )
    )
    return [
        None if handlers is None else any(handler in pending for handler in handlers)
        for handlers in handlers_per_request
    ]


def fail_abandoned_allocation_job(
    job: Job, _connection: Any, exc_type: type[BaseException], _exc_value: Any, _traceback: Any
) -> None:
    """
    RQ failure callback of the jobs of an allocation request; acts only on a job a dead worker
    abandoned, every other failure being handled by ``request_exception_handler``.

    The callback runs from the registry maintenance of a living worker, so an error is logged
    and never raised.

    :param job: The failed stage job or NetBird provisioning job.
    :param exc_type: The type of the failure.
    """
    if not issubclass(exc_type, AbandonedJobError):
        return
    try:
        stage_job = (
            AllocationRQJob.objects.select_related('allocation_stage').filter(job_id=job.id).first()
        )
        if stage_job is not None:
            request = stage_job.allocation_stage.allocation_request_fk_many
        else:
            request = AllocationRequest.objects.get(netbird_job_id=job.id)
        AllocationRequestHandler().fail_abandoned_job(request, job.id)
    except Exception:  # pylint: disable=broad-exception-caught
        LOG.exception('Failing the abandoned allocation job failed', job_id=job.id)


def request_exception_handler(job: Job, _exc_type: Any, _exc_value: Any, _traceback: Any) -> None:
    """
    Handle exception raised during request execution in Redis queue worker.

    :param job: The Job that raised an exception.
    :param exc_type: The type of the exception being handled.
    :param exc_value: The exception instance. Instance of exc_value.
    :param traceback: Traceback object which encapsulates the call stack.
    """
    request_handler: RequestHandler | None = None
    request: SandboxRequest | None = None
    try:
        request = AllocationRQJob.objects.get(
            job_id=job.id
        ).allocation_stage.allocation_request_fk_many
        request_handler = AllocationRequestHandler()
    except AllocationRQJob.DoesNotExist:
        try:
            request = CleanupRQJob.objects.get(job_id=job.id).cleanup_stage.cleanup_request_fk_many
            request_handler = CleanupRequestHandler()
        except CleanupRQJob.DoesNotExist:
            pass

    if request_handler and request:
        try:
            request_handler.cancel_request(request)
        except exceptions.ValidationError:
            LOG.info('Request already finished, nothing to cancel', request=request.id)
        if isinstance(request_handler, CleanupRequestHandler):
            request_handler.retry_request(request)
