"""Stage handler classes for managing sandbox allocation and cleanup stage execution."""

import abc
import contextlib
import os
from collections.abc import Iterator
from subprocess import Popen, TimeoutExpired  # noqa: S404  # no process is started here
from typing import Any, override

import django_rq
import docker.errors
import requests
import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rq.command import send_stop_job_command
from rq.exceptions import InvalidJobOperation, NoSuchJobError
from rq.job import Job, JobStatus, get_current_job
from rq.queue import Queue

from crczp.cloud_commons import CrczpException, StackCreationFailed, StackNameScheme
from crczp.sandbox_ansible_app.lib.ansible import AllocationAnsibleRunner, AnsibleRunner
from crczp.sandbox_ansible_app.models import (
    AnsibleAllocationStage,
    AnsibleCleanupStage,
    Container,
    NetworkingAnsibleAllocationStage,
    UserAnsibleAllocationStage,
    UserAnsibleCleanupStage,
)
from crczp.sandbox_common_lib import exceptions, utils
from crczp.sandbox_definition_app.lib import definitions
from crczp.sandbox_instance_app.lib import netbird, sandboxes, stack_removal
from crczp.sandbox_instance_app.lib.job_liveness import JobLiveness, get_stages_job_liveness
from crczp.sandbox_instance_app.lib.jump_proxy_cleanup import delete_jump_ssh_key
from crczp.sandbox_instance_app.models import (
    AllocationRequest,
    AllocationRQJob,
    AllocationStage,
    AllocationTerraformOutput,
    CleanupRequest,
    CleanupRQJob,
    CleanupStage,
    CleanupTerraformOutput,
    Pool,
    RQJob,
    Sandbox,
    SandboxAllocationUnit,
    StackAllocationStage,
    StackCleanupStage,
    TerraformStack,
)
from crczp.terraform_driver.terraform_exc_handlers import find_state_lock_id
from crczp.terraform_driver.terraform_exceptions import TerraformStateLocked

LOG = structlog.get_logger()
ALLOCATION_JOB_NAME = 'ansible-allocation-{}'
BUILD_STOPPED_MESSAGE = 'Sandbox build stopped before it finished.'
WORKER_DIED_MESSAGE = 'The worker running the stage died before the stage finished.'
JOB_LOST_MESSAGE = 'The job of the stage was lost before the stage finished.'
NETBIRD_JOB_LOST_MESSAGE = 'The NetBird provisioning job was lost before the sandbox was built.'
PRECEDING_STAGE_FAILED_MESSAGE = 'An earlier stage of the request failed, so the stage never ran.'
ABANDONED_STAGE_MESSAGES = frozenset({
    WORKER_DIED_MESSAGE,
    JOB_LOST_MESSAGE,
    NETBIRD_JOB_LOST_MESSAGE,
})
PROCESS_STOP_GRACE_SECONDS = 30
"""
How long an interrupted OpenTofu process may take to release its state lock and exit before
it is killed; shorter than the margin RQ gives a timed-out job before killing its work horse.
"""


class CancellableJob(abc.ABC):
    """
    Stops one RQ job on cancellation and records when its cancellation ends.
    """

    def __init__(self) -> None:
        self.stop_sent = False

    @property
    @abc.abstractmethod
    def job_id(self) -> str | None:
        """The ID of the RQ job, or None when there is none to stop."""

    @property
    @abc.abstractmethod
    def finished(self) -> bool:
        """Whether there is nothing left to cancel."""

    @property
    @abc.abstractmethod
    def enqueue_pending(self) -> bool:
        """Whether the job is yet to be queued, so that there is no job ID to stop yet."""

    @abc.abstractmethod
    def refresh(self) -> None:
        """Reload the state the job ID is read from, to see a job queued meanwhile."""

    @abc.abstractmethod
    def request_stop(self) -> None:
        """Stop the job and start the cancellation of what it belongs to."""

    @abc.abstractmethod
    def mark_cancelled(self) -> None:
        """Record that the cancellation ended, unless the job has finished meanwhile."""

    def _stop_job(self) -> None:
        """
        Delete the job, with its dependents unless it has finished, while no worker has taken
        it, else send it the stop command.

        A queued job is taken off its queue first, which no worker can interleave with:
        a job that a worker took meanwhile gets the stop command instead. The dependents of a
        finished job are no longer waiting for it and are left alone.
        """
        job = self._fetch_job()
        if job is None:
            return
        status = job.get_status()
        if status == JobStatus.STARTED or (status == JobStatus.QUEUED and not _take_off_queue(job)):
            self._send_stop(job)
        else:
            job.delete(delete_dependents=status != JobStatus.FINISHED)

    def _fetch_job(self) -> Job | None:
        """
        Fetch the RQ job, or None when there is none or Redis lost it.
        """
        job_id = self.job_id
        if job_id is None:
            return None
        try:
            return Job.fetch(job_id, connection=django_rq.get_connection())
        except NoSuchJobError:
            return None

    def _send_stop(self, job: Job) -> None:
        """
        Send the RQ stop command for the job and delete its dependents.

        The stop counts as sent only when a worker was executing the job; a job that no
        worker executes yet, or that has gone, gets no command.
        """
        try:
            send_stop_job_command(django_rq.get_connection(), job.id)
        except (InvalidJobOperation, NoSuchJobError):
            LOG.info('Stop command not sent, the job is not executing', job_id=job.id)
        else:
            self.stop_sent = True
            self._record_stop_sent()
        job.delete_dependents()

    @abc.abstractmethod
    def _record_stop_sent(self) -> None:
        """Record that the stop command was sent."""


def _take_off_queue(job: Job) -> bool:
    """Remove the job from its queue and return whether it was still there."""
    return job.origin is not None and bool(Queue(job.origin, connection=job.connection).remove(job))


class StageHandler(CancellableJob):
    """
    Handles DB Stage object and generalizes its common tasks.

    WARNING: Always set _job_class attribute with type inherited from RQJob
      in all non-abstract descendants.
    """

    _job_class: type[RQJob]

    def __init__(self, stage: Any, name: str | None = None):
        super().__init__()
        self.stage = stage
        self.name = name if name is not None else self.stage.__class__.__name__

    @property
    @override
    def job_id(self) -> str | None:
        job_row = getattr(self.stage, 'rq_job', None)
        return job_row.job_id if job_row else None

    @property
    @override
    def finished(self) -> bool:
        return self.stage.finished

    @property
    @override
    def enqueue_pending(self) -> bool:
        return self.stage.enqueue_pending

    @override
    def refresh(self) -> None:
        self.stage.refresh_from_db()

    def execute(self) -> None:
        """
        Handles stage execution defined in method _execute.

        Sets the start and the end time of the stage execution, saves any error messages
          of all caught exceptions, and re-raise them again. A failed allocation stage counts
          its sandbox build as failed in the request group.
        A stage that is already finished, such as one cancelled before its job was queued,
          is not executed, and the jobs that depend on its job are deleted.
        Once the stage succeeded and its row is saved, _finalize runs.
        Warning: Do not override this method. Any changes should be made to _execute method.
        """
        self.stage.refresh_from_db(fields=['finished'])
        if self.stage.finished:
            LOG.info('Stage already finished, skipping it', stage_name=self.name, stage=self.stage)
            current_job = get_current_job()
            if current_job is not None:
                current_job.delete_dependents()
            return None
        try:
            LOG.info(f'Stage {self.name} started', stage=self.stage)
            self.stage.start = timezone.now()
            self.stage.save()
            self._execute()
        except Exception as ex:
            self.stage.failed = True
            self.stage.error_message = str(ex)
            self._count_build_failure(ex)
            raise
        finally:
            self.stage.end = timezone.now()
            self.stage.finished = True
            self.stage.save()
            LOG.info(f'Stage {self.name} ended', stage=self.stage)
        self._finalize()
        return None

    def set_job_id(self, job_id: str) -> None:
        """
        Set Job ID once execution of this stage is enqueued.

        :param job_id: The ID of enqueues Job that will execute this stage.
        """
        if hasattr(self._job_class, 'allocation_stage'):
            self._job_class.objects.create(allocation_stage=self.stage, job_id=job_id)
        elif hasattr(self._job_class, 'cleanup_stage'):
            self._job_class.objects.create(cleanup_stage=self.stage, job_id=job_id)
        else:
            LOG.warning(f"Unknown Job class '{self._job_class}'. Job ID '{job_id}' was not set")

    @abc.abstractmethod
    def _execute(self) -> None:
        """
        Execute the stage.
        """

    def _finalize(self) -> None:
        """
        Run what follows a succeeded stage once its row is saved; nothing by default.
        """

    @override
    def request_stop(self) -> None:
        """
        Stop the job of the stage and run the stage-specific cancellation in _cancel.

        A job that is not executing is deleted together with its dependents. An executing job
          gets the RQ stop command, which makes its worker kill the job's whole process group;
          its dependents are deleted and the stage records when the stop was first requested.
          The stage stays unfinished until mark_cancelled.
        Warning: Do not override this method. Any changes should be made to _cancel method.
        """
        LOG.info('Stop of stage requested', stage_name=self.name, stage=self.stage)
        if self.job_id is None:
            LOG.warning(f'Stage {self.name} does not have an RQ job')
        self._stop_job()
        self._cancel()

    @override
    def mark_cancelled(self) -> None:
        """
        Mark the stage failed and finished, unless its job has finished it meanwhile.

        A stage marked here counts its sandbox build as failed in the request group, and runs
          _cancel once more, as its job can no longer start what the first run missed.
        """
        self._fail_unfinished(exceptions.InterruptError(BUILD_STOPPED_MESSAGE))

    def mark_abandoned(
        self, reason: str = WORKER_DIED_MESSAGE, *, unless_stop_requested: bool = False
    ) -> bool:
        """
        Mark the stage failed and finished with the reason its job was abandoned, unless its
        job has finished it meanwhile; counts and cleans up as mark_cancelled does.

        :param reason: The error message the stage keeps; by default that its worker died.
        :param unless_stop_requested: Leave a stage whose stop was requested to that stop.
        :returns: Whether the stage was marked.
        """
        return self._fail_unfinished(
            exceptions.InterruptError(reason), unless_stop_requested=unless_stop_requested
        )

    def _fail_unfinished(self, failure: Exception, *, unless_stop_requested: bool = False) -> bool:
        """
        Mark the stage failed and finished while it is unfinished, in one conditional update,
        and count and clean up after it when this call marked it.

        :returns: Whether this call marked the stage.
        """
        stage_table_model = (
            AllocationStage if isinstance(self.stage, AllocationStage) else CleanupStage
        )
        unfinished = stage_table_model.objects.filter(pk=self.stage.pk, finished=False)
        if unless_stop_requested:
            unfinished = unfinished.filter(stop_requested_at__isnull=True)
        marked = unfinished.update(
            failed=True, error_message=str(failure), end=timezone.now(), finished=True
        )
        self.stage.refresh_from_db()
        if marked:
            self._count_build_failure(failure)
            self._cancel()
        LOG.info('Failure of stage recorded', stage_name=self.name, stage=self.stage)
        return bool(marked)

    def _count_build_failure(self, failure: Exception) -> None:
        """
        Count the sandbox build of an allocation stage as failed in its request group.
        """
        if isinstance(self.stage, AllocationStage):
            self.stage.allocation_request_fk_many.count_build_in_request_group(failure)

    @override
    def _record_stop_sent(self) -> None:
        """
        Record on the stage when the stop was first requested.
        """
        if self.stage.stop_requested_at is None:
            self.stage.stop_requested_at = timezone.now()
            self.stage.save(update_fields=['stop_requested_at'])

    @abc.abstractmethod
    def _cancel(self) -> None:
        """
        Cancel the stage
        """


class NetbirdProvisioningHandler(CancellableJob):
    """
    Cancels the NetBird provisioning job of an allocation request, which no stage tracks.
    """

    def __init__(self, allocation_request: AllocationRequest):
        super().__init__()
        self.allocation_request = allocation_request

    @property
    @override
    def job_id(self) -> str | None:
        return self.allocation_request.netbird_job_id

    @property
    @override
    def finished(self) -> bool:
        return self.job_id is None and not self.enqueue_pending

    @property
    @override
    def enqueue_pending(self) -> bool:
        """
        Whether the provisioning job may be yet to be queued: it is queued together with the
        stage jobs of the request, and a stage of the request still waits for its job.
        """
        return self.job_id is None and any(
            stage.enqueue_pending
            for stage in AllocationStage.objects.filter(
                allocation_request_fk_many=self.allocation_request, rq_job__isnull=True
            )
        )

    @override
    def refresh(self) -> None:
        self.allocation_request.refresh_from_db(fields=['netbird_job_id'])

    @override
    def request_stop(self) -> None:
        """
        Stop the provisioning job, deleting it with its dependents when it is not executing.
        """
        LOG.info('Stop of NetBird provisioning requested', job_id=self.job_id)
        self._stop_job()

    @override
    def _record_stop_sent(self) -> None:
        """
        Records nothing: the request keeps the job id until the job has ended.
        """

    @override
    def mark_cancelled(self) -> None:
        """
        Forget the provisioning job, which has ended.
        """
        AllocationRequest.objects.filter(pk=self.allocation_request.pk).update(netbird_job_id=None)
        self.allocation_request.netbird_job_id = None
        LOG.info('Cancellation of NetBird provisioning ended', request=self.allocation_request)


@contextlib.contextmanager
def _process_ended_on_exit(process: Popen[str]) -> Iterator[None]:
    """
    Leave the block only once the process has exited, ending a process still running when
    the block is cut short, as by the job timeout of its RQ job.
    """
    try:
        yield
    finally:
        _end_process(process)


def _end_process(process: Popen[str]) -> None:
    """
    Interrupt a running OpenTofu process, so that it stops and releases its state lock, kill
    it when it outlives the grace period, and return once it has exited.

    The output it prints meanwhile is read and dropped, so that a full pipe cannot hold it.
    """
    if process.poll() is not None:
        return
    LOG.warning('Stopping the OpenTofu process', pid=process.pid)
    process.terminate()
    try:
        process.communicate(timeout=PROCESS_STOP_GRACE_SECONDS)
    except TimeoutExpired:
        LOG.warning('Killing the OpenTofu process', pid=process.pid)
        process.kill()
        process.wait()


class StackStageHandler(StageHandler):
    """
    Generalizes common tasks of stages manipulating with OpenStack stacks.
    """

    _client = utils.get_terraform_client()

    @override
    @abc.abstractmethod
    def _execute(self) -> None:
        """Execute the stack stage."""

    @override
    @abc.abstractmethod
    def _cancel(self) -> None:
        """Cancel the stack stage."""

    def _log_process_output(self, process: Popen[str], terraform_output: Any, **kwargs: Any) -> str:
        """
        Store each line the process prints and return the whole output.
        """
        lines = []
        for line in self._client.get_process_output(process):
            line = line.rstrip()
            LOG.debug(line)
            terraform_output.objects.create(**kwargs, content=line)
            lines.append(line)
        return '\n'.join(lines)

    def _wait_for_process(
        self,
        process: Popen[str],
        timeout: int = settings.CRCZP_CONFIG.sandbox_build_timeout,
        logged_output: str = '',
    ) -> None:
        """
        Wait for process to finish.

        A failure caused by a held state lock raises TerraformStateLocked with the lock id,
          read from the output already logged or from the rest of the process output.
        """
        stdout, _stderr, return_code = self._client.wait_for_process(process, timeout)
        if return_code:
            LOG.error('Terraform execution failed', return_code=return_code)
            lock_id = find_state_lock_id(f'{logged_output}\n{stdout or ""}')
            if lock_id:
                raise TerraformStateLocked(lock_id)
            raise CrczpException('Terraform execution failed. See logs for details.')

    def _may_release_state_lock(self, allocation_unit: SandboxAllocationUnit) -> bool:  # pylint: disable=unused-argument
        """
        Whether no job of the allocation unit can still hold the state lock of its stack.
        """
        return False

    def _destroy_stack(self, stack_name: str, log_output: bool) -> bool:
        """
        Run the destroy of the stack and wait for it to finish.

        :returns: Whether the destroy ran; False when the stack has no OpenTofu state to
            destroy, which delete_stack signals by starting no process.
        """
        process = self._client.delete_stack(stack_name)
        if process is None:
            LOG.warning(
                'The deletion of the stack failed. Terraform could not initialize directory'
            )
            return False
        with _process_ended_on_exit(process):
            logged_output = (
                self._log_process_output(process, CleanupTerraformOutput, cleanup_stage=self.stage)
                if log_output
                else ''
            )
            self._wait_for_process(process, logged_output=logged_output)
        return True

    def _destroy_stack_releasing_stale_lock(
        self, allocation_unit: SandboxAllocationUnit, log_output: bool
    ) -> bool:
        """
        Destroy the stack of the allocation unit through its OpenTofu state.

        A destroy blocked by a state lock that no job of the unit can hold any more releases
          the lock and runs once more.

        :returns: Whether the destroy ran; False when the stack has no OpenTofu state.
        :raise TerraformStateLocked: The state is locked by a job that may still be alive.
        :raise CrczpException: The destroy fails.
        """
        stack_name = allocation_unit.get_stack_name()
        try:
            return self._destroy_stack(stack_name, log_output)
        except TerraformStateLocked as locked:
            if not self._may_release_state_lock(allocation_unit):
                raise
            LOG.warning('Releasing stale state lock', stack_name=stack_name, lock=locked.lock_id)
            self._client.force_unlock_stack(stack_name, locked.lock_id)
            return self._destroy_stack(stack_name, log_output)

    def _delete_stack(
        self, allocation_unit: SandboxAllocationUnit, log_output: bool = True
    ) -> None:
        """
        Delete stack associated with the given allocation unit.
        """
        stack_name = allocation_unit.get_stack_name()

        LOG.debug(
            'Starting Stack delete in OpenStack',
            stack_name=stack_name,
            allocation_unit=allocation_unit,
        )

        try:
            self._destroy_stack_releasing_stale_lock(allocation_unit, log_output)
        except CrczpException as exc:
            raise exceptions.StackError(f'Sandbox deletion failed :{exc}') from exc

        stack_removal.delete_workspace(self._client, stack_name, force=False)
        LOG.debug(
            'Deleting local terraform stack directory',
            stack_name=stack_name,
            allocation_unit=allocation_unit,
        )
        stack_removal.delete_stack_directory(self._client, stack_name)


class AllocationStackStageHandler(StackStageHandler):
    """
    Specifies tasks needed for OpenStack stack allocation and its cancellation.
    """

    stage: StackAllocationStage
    _job_class: type[AllocationRQJob] = AllocationRQJob

    def __init__(self, stage: Any):
        self.process = None
        super().__init__(stage)

    @override
    def _execute(self) -> None:
        """
        Allocate stack in the OpenStack cloud platform.

        The OpenTofu process has exited whenever this returns or raises.
        """
        allocation_unit = self.stage.allocation_request.allocation_unit
        stack_name = allocation_unit.get_stack_name()
        pool = allocation_unit.pool
        definition = pool.definition
        top_def = definitions.get_definition(definition.url, pool.rev_sha, settings.CRCZP_CONFIG)
        definitions.validate_build_requirements(top_def)
        try:
            self.process = self._client.create_stack(
                top_def,
                stack_name=stack_name,
                key_pair_name_ssh=allocation_unit.pool.ssh_keypair_name,
                key_pair_name_cert=allocation_unit.pool.certificate_keypair_name,
            )
            if self.process is None:
                raise CrczpException('Process was not created.')
            with _process_ended_on_exit(self.process):
                TerraformStack.objects.create(
                    allocation_stage=self.stage, stack_id=self.process.pid
                )
                self._log_process_output(
                    self.process, AllocationTerraformOutput, allocation_stage=self.stage
                )
                self._wait_for_process(self.process)
        except CrczpException as exc:
            try:
                super()._delete_stack(allocation_unit, log_output=False)
            except (CrczpException, exceptions.StackError) as cleanup_exc:
                LOG.warning('Stack removal after failed build failed', error=str(cleanup_exc))
            raise StackCreationFailed(f'Sandbox build failed: {exc}') from exc

    @override
    def _cancel(self) -> None:
        """
        Stop the OpenStack stack allocation.

        INFO: Nothing to be done. The stop of the stage job kills its OpenTofu process.
        """


class CleanupStackStageHandler(StackStageHandler):
    """
    Specifies tasks needed for OpenStack stack deletion and its cancellation.
    """

    stage: StackCleanupStage
    _job_class: type[CleanupRQJob] = CleanupRQJob

    @override
    def _execute(self) -> None:
        """
        Delete allocated stack in the OpenStack platform.

        The stack is destroyed through its OpenTofu state. Where the destroy fails or the stack
          has no state, the cloud resources of the stack are deleted by name instead. Then the
          OpenTofu workspace and the local stack directory are removed. A destroy blocked by
          a state lock that a job may still hold fails the stage, deleting nothing by name.

        :raise StackError: The destroy is blocked by a live lock, or resources remain.
        """
        allocation_unit = self.stage.cleanup_request.allocation_unit
        stack_name = allocation_unit.get_stack_name()
        try:
            destroyed = self._destroy_stack_releasing_stale_lock(allocation_unit, True)
        except TerraformStateLocked as locked:
            raise exceptions.StackError(f'Sandbox deletion failed :{locked}') from locked
        except CrczpException as exc:
            destroy_error = str(exc)
        else:
            destroy_error = None if destroyed else 'The stack has no OpenTofu state.'

        if destroy_error is not None:
            LOG.warning(
                'Deleting the resources of the stack by name',
                stack_name=stack_name,
                reason=destroy_error,
            )
            failures = stack_removal.delete_resources_by_name(
                self._client,
                stack_name,
                StackNameScheme(settings.CRCZP_SERVICE_CONFIG.stack_name_prefix),
            )
            if failures:
                raise exceptions.StackError(
                    f'Sandbox deletion failed: {destroy_error} '
                    f'Resources left: {"; ".join(failures)}.'
                )
        stack_removal.delete_workspace(self._client, stack_name, force=destroy_error is not None)
        stack_removal.delete_stack_directory(self._client, stack_name)

    @override
    def _finalize(self) -> None:
        """
        Remove the allocation unit, as the last step of the last cleanup stage.

        The unit is deleted, which cascades its cleanup request and stage rows, and the pool
          size is decremented in one transaction under the pool lock. The pool is deleted when
          this was its last unit and the cleanup request asks for it.
        """
        request = self.stage.cleanup_request
        allocation_unit = request.allocation_unit
        with transaction.atomic():
            pool = Pool.objects.select_for_update().get(id=allocation_unit.pool_id)
            delete_pool = CleanupRequest.objects.filter(pk=request.pk, delete_pool=True).exists()
            deleted_count, _ = SandboxAllocationUnit.objects.filter(pk=allocation_unit.pk).delete()
            if not deleted_count:
                LOG.info('Allocation Unit already deleted', allocation_unit_id=allocation_unit.pk)
                return
            pool.size -= 1
            pool.save()

        LOG.info('Allocation Unit deleted from DB', allocation_unit=allocation_unit)
        if pool.size == 0 and delete_pool:
            self._delete_empty_pool(pool)

    @staticmethod
    def _delete_empty_pool(pool: Pool) -> None:
        """Delete the pool after its final cleanup; a pool that cannot be deleted stays."""
        # pools imports the request handlers, which import this module.
        from crczp.sandbox_instance_app.lib import pools  # pylint: disable=import-outside-toplevel

        try:
            pools.delete_pool(pool)
        except exceptions.ValidationError as exc:
            LOG.warning('Pool not deleted by its final cleanup', pool=pool, error=str(exc))
        else:
            LOG.info('Pool deleted from DB by final cleanup finishing', pool=pool)

    @override
    def _may_release_state_lock(self, allocation_unit: SandboxAllocationUnit) -> bool:
        """
        True when no job of the unit's stack allocation stage can still hold the state lock:
        every stage of it that is unfinished has a job that is gone.
        """
        unfinished_stages = StackAllocationStage.objects.filter(
            allocation_request__allocation_unit=allocation_unit, finished=False
        ).select_related('rq_job')
        return all(
            liveness is JobLiveness.DEAD for liveness in get_stages_job_liveness(unfinished_stages)
        )

    @override
    def _cancel(self) -> None:
        """
        Stop the deletion of the OpenStack stack.

        INFO: Nothing to be done. Deletion is irreversible operation.
        """


class AnsibleStageHandler(StageHandler):
    """
    Generalizes common tasks of stages executing Ansible tasks on the remote infrastructure.
    """

    def __init__(
        self,
        stage: AnsibleCleanupStage | AnsibleAllocationStage,
        name: str | None = None,
    ):
        super().__init__(stage, name)
        self.stage = stage

    @override
    @abc.abstractmethod
    def _execute(self) -> None: ...

    @override
    @abc.abstractmethod
    def _cancel(self) -> None: ...

    def create_directory_path(self, allocation_unit: SandboxAllocationUnit) -> str:
        """
        Compose absolute path to directory for Docker container volumes.
        """
        return os.path.join(
            settings.CRCZP_CONFIG.ansible_runner_settings.volumes_path,
            allocation_unit.get_stack_name(),
            f'{self.stage.id}-{utils.get_simple_uuid()}',
        )

    def check_status(self, status: dict[str, Any]) -> None:
        """
        Check status returned by Docker container
        and raise an exception if Ansible execution failed.
        """
        status_code = status['StatusCode']
        if status_code != 0:
            raise exceptions.AnsibleError(
                f"Ansible ID={self.stage.id} failed with status code '{status_code}'"
            )


class AllocationAnsibleStageHandler(AnsibleStageHandler):
    """
    Specifies tasks needed for Ansible execution on the remote infrastructure and its cancellation.
    """

    stage: AnsibleAllocationStage
    _job_class: type[AllocationRQJob] = AllocationRQJob

    def __init__(
        self,
        stage: AnsibleAllocationStage,
        sandbox: Sandbox | None = None,
        name: str | None = None,
    ):
        super().__init__(stage, name)

        self.allocation_unit = stage.allocation_request_fk_many.allocation_unit
        self.directory_path = self.create_directory_path(self.allocation_unit)
        self.sandbox = sandbox

    @override
    def _execute(self) -> None:
        """
        Prepare and execute the Ansible playbooks on the remote infrastructure.

        The container is recorded under its generated name before it starts, so that
          cancellation finds it whenever it runs; the record is dropped once the container is
          gone.
        """
        if self.sandbox is None:
            raise exceptions.AnsibleError('Sandbox was not provided')

        if isinstance(self.stage, NetworkingAnsibleAllocationStage):
            # Runs before the inventory/SSH dir below read sandbox.role_keypairs,
            # so every row they need already exists; a failure here fails this
            # stage, tracked and reported like any other stage failure.
            sandboxes.generate_role_keypairs(self.sandbox, self.allocation_unit.pool)

        runner = AllocationAnsibleRunner(self.directory_path)
        runner.prepare_ssh_dir(self.allocation_unit.pool, self.sandbox)
        runner.prepare_inventory_file(self.sandbox)
        runner.prepare_containers_directory(self.sandbox)
        runner.prepare_git_credentials(settings.CRCZP_CONFIG)

        container_name = ALLOCATION_JOB_NAME.format(f'{self.stage.id}-{utils.get_simple_uuid()}')
        container_record = Container.objects.create(
            allocation_stage=self.stage, container_name=container_name
        )
        try:
            container = runner.run_ansible_playbook(
                self.stage.repo_url, self.stage.rev, self.stage, container_name
            )
            try:
                container.get_container_outputs()
                container.check_container_status()
            finally:
                container.delete()
        finally:
            container_record.delete()

    @override
    def _finalize(self) -> None:
        """
        Mark the sandbox ready and count its build as finished in the request group once the
          user stage, the last allocation stage, succeeded.

        A sandbox deleted meanwhile stays deleted.
        """
        if isinstance(self.stage, UserAnsibleAllocationStage) and self.sandbox is not None:
            Sandbox.objects.filter(pk=self.sandbox.pk).update(ready=True)
            LOG.debug(
                f'Allocation {self.allocation_unit.id} finished,'
                ' incrementing finished allocation count.'
            )
            self.stage.allocation_request_fk_many.count_build_in_request_group()

    @override
    def _cancel(self) -> None:
        """
        Delete the Ansible container of the stage and its record.

        A container that cannot be deleted, as one yet to start or one the container runtime does
          not answer for, keeps its record for the next run.
        """
        container = Container.objects.filter(allocation_stage=self.stage).first()
        if container is None:
            return
        try:
            AnsibleRunner(self.directory_path).delete_container(container.container_name)
        except (
            docker.errors.DockerException,
            requests.exceptions.RequestException,
            CrczpException,
        ) as ex:
            LOG.warning('Cancelling Ansible', exception=str(ex), stage=self.stage)
            return
        container.delete()


class CleanupAnsibleStageHandler(AnsibleStageHandler):
    """
    Specifies tasks needed for resources cleanup created during the Ansible execution.
    """

    stage: AnsibleCleanupStage
    _job_class: type[CleanupRQJob] = CleanupRQJob

    def __init__(self, stage: AnsibleCleanupStage):
        super().__init__(stage)

        self.allocation_unit = stage.cleanup_request_fk_many.allocation_unit
        self.directory_path = self.create_directory_path(self.allocation_unit)

    @override
    def _execute(self) -> None:
        """
        Clean up resources created during the Ansible execution.

        The networking stage removes the access paths into the sandbox: its NetBird
        resources and its home on the jump host. Both removals run, and the stage fails
        when either leaves something behind.

        :raise NetworkError: A NetBird object or the jump host home could not be removed.
        """
        if isinstance(self.stage, UserAnsibleCleanupStage):
            return
        allocation_unit = self.stage.cleanup_request_fk_many.allocation_unit
        failures = netbird.destroy_netbird_for_unit(allocation_unit)
        try:
            delete_jump_ssh_key(allocation_unit)
        except exceptions.NetworkError as exc:
            failures.append(str(exc))
        if failures:
            raise exceptions.NetworkError(
                f'Networking cleanup left objects behind: {"; ".join(failures)}.'
            )

    @override
    def _cancel(self) -> None:
        """
        Stop the resources clean up.

        INFO: Nothing to be done. The clean up is irreversible operation.
        """
