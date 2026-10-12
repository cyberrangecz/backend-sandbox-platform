"""
Pool Service module for Pool management.
"""

import hmac
import io
import zipfile
from typing import Any

import structlog
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import transaction
from django.db.models import ProtectedError, QuerySet
from django.shortcuts import get_object_or_404

from crczp.cloud_commons import (
    CrczpException,
    HardwareUsage,
    StackCreationFailed,
)
from crczp.sandbox_common_lib import exceptions, utils
from crczp.sandbox_definition_app.lib import definitions
from crczp.sandbox_definition_app.models import Definition
from crczp.sandbox_instance_app import serializers
from crczp.sandbox_instance_app.lib import requests, sandboxes, unit_filters
from crczp.sandbox_instance_app.models import (
    Pool,
    PoolLock,
    Sandbox,
    SandboxAllocationUnit,
    SandboxLock,
)

LOG = structlog.get_logger()
POOL_CACHE_TIMEOUT = None
PROJECT_LIMITS_CACHE_IDENTIFIER = 'project-limits'
POOL_CACHE_PREFIX = 'hardware-usage-pool-{}'


def get_pool(pool_pk: int) -> Pool:
    """
    Retrieves Pool instance from DB (or raises 404).
    Alternative to self.get_object method of APIView classes.

    :param pool_pk: Pool primary key (ID)
    :return: Pool instance from DB
    :raise Http404: if Pool does not exist
    """
    return get_object_or_404(Pool, pk=pool_pk)


def create_pool(data: dict[str, Any], created_by: User | None) -> Pool:
    """
    Creates new Pool instance.
    Also creates management key-pairs in OpenStack

    :param data: dict of attributes to create model. Currently must contain:
        - definition: primary key (ID) of the Definition that new Pool instance is related to
        - max_size: max size of new Pool instance
    :param created_by: User creating pool
    :return: new Pool instance
    """
    definition = get_object_or_404(Definition, pk=data.get('definition_id'))
    provider = definitions.get_def_provider(definition.url, settings.CRCZP_CONFIG)
    rev_sha = provider.get_rev_sha(definition.rev)

    data['rev'] = definition.rev
    data['rev_sha'] = rev_sha

    serializer = serializers.PoolSerializerCreate(data=data)
    serializer.is_valid(raise_exception=True)

    client = utils.get_terraform_client()
    top_def = definitions.get_definition(definition.url, rev_sha, settings.CRCZP_CONFIG)
    definitions.validate_build_requirements(top_def)
    definitions.validate_topology_definition(top_def)
    containers = definitions.get_containers(definition.url, rev_sha, settings.CRCZP_CONFIG)
    if containers:
        definitions.validate_docker_containers(definition.url, rev_sha, settings.CRCZP_CONFIG)
    client.validate_topology_definition(top_def)

    private_key, public_key = utils.generate_ssh_keypair()
    if settings.AWS_PROVIDER_CONFIGURED:
        certificate = ''
    else:
        certificate = utils.create_self_signed_certificate(private_key)

    pool = serializer.save(
        created_by=created_by,
        private_management_key=private_key,
        public_management_key=public_key,
        management_certificate=certificate,
    )

    # Key-pair names are derived from the pool's id, so the row has to exist first.
    try:
        client.create_keypair(pool.ssh_keypair_name, public_key, 'ssh')
        if certificate:
            client.create_keypair(pool.certificate_keypair_name, certificate, 'x509')
    except Exception:
        pool_id = pool.id
        try:
            delete_pool(pool)
        except Exception as cleanup_exc:  # pylint: disable=broad-exception-caught
            LOG.warning(
                'Pool removal after failed key-pair creation failed',
                pool_id=pool_id,
                error=str(cleanup_exc),
            )
        raise

    return pool


def delete_pool(pool: Pool) -> None:
    """Deletes given Pool, deletes management key-pair in OpenStack and cache record for pool"""
    ssh_keypair_name = pool.ssh_keypair_name
    certificate_keypair_name = pool.certificate_keypair_name
    pool_cache_key = get_cache_key(pool)

    try:
        pool.delete()
        utils.clear_cache(pool_cache_key)
    except ProtectedError as e:
        error_message = str(e)
        if 'PoolLock' in error_message:
            raise exceptions.ValidationError(f'Cannot delete locked pool (ID="{pool.id}").') from e
        if 'AllocationUnit' in error_message:
            raise exceptions.ValidationError(
                f'Cannot delete non-empty pool (ID="{pool.id}"). '
                'Delete all allocation units before deleting the pool.'
            ) from e
        raise exceptions.ValidationError('Unknown error: ' + error_message) from e

    client = utils.get_terraform_client()
    try:
        client.delete_keypair(ssh_keypair_name)
    except CrczpException as exc:
        LOG.warning(exc)

    try:
        client.delete_keypair(certificate_keypair_name)
    except CrczpException as exc:
        LOG.warning(exc)


def get_sandboxes_in_pool(pool: Pool) -> QuerySet[Sandbox, Sandbox]:
    """Returns DB QuerySet of sandboxes from given pool."""
    alloc_unit_ids = [unit.id for unit in pool.allocation_units.all()]
    return Sandbox.objects.all().filter(allocation_unit_id__in=alloc_unit_ids)


def validate_hardware_usage_of_sandboxes(pool: Pool, count: int) -> None:
    """
    Validates Heat Stacks hardware usage of sandboxes against OpenStack limits.

    :param pool: Pool in which sandboxes are built.
    :param count: Number of sandboxes.
    :return: None
    :raise: StackError if limits are exceeded.
    """
    try:
        top_def = definitions.get_definition(
            pool.definition.url, pool.rev_sha, settings.CRCZP_CONFIG
        )
        client = utils.get_terraform_client()
        topology_instance = client.get_topology_instance(top_def)
        client.validate_hardware_usage_of_stacks(topology_instance, count)
    except StackCreationFailed as exc:
        raise exceptions.StackError(f'Cannot build {count} sandboxes: {exc}') from exc


def create_sandboxes_in_pool(
    pool: Pool,
    created_by: User | None,
    count: int | None = None,
    *,
    created_by_sub: str | None = None,
) -> list[SandboxAllocationUnit]:
    """
    Creates count sandboxes in given pool.

    :param pool: Pool where to build sandbox
    :param created_by: User initiating the build.
    :param count: Count of sandboxes, None to build maximum
    :param created_by_sub: OIDC sub of a trainee allocating a sandbox for themselves. A
        trainee may hold one active sandbox per pool.
    :return: sandbox instance
    :raises ConflictError: The trainee already has an active sandbox in the pool.
    """
    with transaction.atomic():
        # The pool row lock also serializes the check below with concurrent allocations.
        pool = Pool.objects.select_for_update().get(pk=pool.id)

        if (
            created_by_sub is not None
            and SandboxAllocationUnit.objects.filter(
                unit_filters.active(), pool=pool, created_by_sub=created_by_sub
            ).exists()
        ):
            raise exceptions.ConflictError(
                'You already have a sandbox in this pool. Use it, or clean it up first.'
            )

        current_size = pool.size
        if count is None:
            count = pool.max_size - current_size

        if current_size + count > pool.max_size:
            raise exceptions.ValidationError(
                f'Current pool size is {current_size}/{pool.max_size},'
                f' cannot build {count} more sandboxes'
            )

        validate_hardware_usage_of_sandboxes(pool, count)
        units = requests.create_allocations_requests(
            pool, count, created_by, created_by_sub=created_by_sub
        )
        pool.size += count
        pool.save()
        return units


def get_unlocked_sandbox(pool: Pool, created_by: User | None) -> Sandbox | None:
    """Lock and return a free sandbox of the pool, or None when every sandbox is taken.

    Only ready sandboxes an organizer built are handed out: a sandbox a trainee allocated
    for themselves is theirs, even though it carries no lock. Sandboxes another request is
    locking right now are skipped instead of waited for, so the trainees of one training
    starting together do not queue behind each other.
    """
    with transaction.atomic():
        if created_by is not None:
            # Serializes the requests of one user, so two concurrent ones cannot each lock a
            # sandbox; the check below then sees the first one's lock.
            User.objects.select_for_update().filter(pk=created_by.pk).first()
        if _has_locked_sandbox(pool, created_by):
            raise CrczpException(
                'You already have a sandbox assigned. Use that one or ask your tutor for help.'
            )
        free_sandboxes = (
            Sandbox.objects
            .filter(
                allocation_unit__pool=pool,
                allocation_unit__created_by_sub__isnull=True,
                ready=True,
                lock__isnull=True,
            )
            .order_by('id')
            # Lock only the sandbox row: FOR UPDATE cannot cover the nullable side of the
            # outer join that lock__isnull needs.
            .select_for_update(skip_locked=True, of=('self',))
        )
        while (sandbox := free_sandboxes.first()) is not None:
            # A request that locked this sandbox and committed after the query above took its
            # snapshot is invisible to it; a new statement sees its lock. The next query then
            # excludes this sandbox, so the loop ends.
            if not SandboxLock.objects.filter(sandbox=sandbox).exists():
                SandboxLock.objects.create(sandbox=sandbox, created_by=created_by)
                return sandbox
        return None


def _has_locked_sandbox(pool: Pool, created_by: User | None) -> bool:
    """Check if the user holds the lock of a sandbox in the pool."""
    if created_by is None:
        return False
    return Sandbox.objects.filter(allocation_unit__pool=pool, lock__created_by=created_by).exists()


def lock_pool(pool: Pool, training_access_token: str | None = None) -> PoolLock:
    """Lock given Pool. Raise ValidationError if already locked."""
    with transaction.atomic():
        pool = Pool.objects.select_for_update().get(pk=pool.id)
        if hasattr(pool, 'lock'):
            raise exceptions.ValidationError('Pool already locked.')
        return PoolLock.objects.create(pool=pool, training_access_token=training_access_token)


def validate_training_access_token(pool: Pool, training_access_token: str | None) -> None:
    """Check that the pool is locked for a training and the token is that training's.

    :raises ValidationError: The pool is not locked.
    :raises ForbiddenError: No training holds the pool, or the token is not its one.
    """
    if not hasattr(pool, 'lock'):
        raise exceptions.ValidationError('The pool is not locked.')
    expected_token = pool.lock.training_access_token
    if expected_token is None:
        raise exceptions.ForbiddenError('This pool does not have a training assigned')
    if training_access_token is None or not hmac.compare_digest(
        expected_token.encode(), training_access_token.encode()
    ):
        raise exceptions.ForbiddenError('Provided training access token is not valid.')


def get_management_ssh_access(pool: Pool) -> io.BytesIO:
    """Get management SSH access files."""
    ssh_access_name = f'pool-id-{pool.id}'
    private_key_name = f'{ssh_access_name}-management-key'
    public_key_name = f'{private_key_name}.pub'

    in_memory_zip_file = io.BytesIO()
    with zipfile.ZipFile(in_memory_zip_file, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for sandbox in get_sandboxes_in_pool(pool):
            tmp = f'{ssh_access_name}-sandbox-id-{sandbox.id}-management'
            ssh_config_name = f'{tmp}-config'

            ssh_config = sandboxes.get_management_sshconfig(sandbox, f'~/.ssh/{private_key_name}')

            zip_file.writestr(ssh_config_name, ssh_config.serialize())

        zip_file.writestr(private_key_name, pool.private_management_key)
        zip_file.writestr(public_key_name, pool.public_management_key)

    in_memory_zip_file.seek(0)
    return in_memory_zip_file


def _get_hardware_usage(url: str, rev: str) -> HardwareUsage | None:
    """
    Get Heat Stack hardware usage calculated from topology definition.

    :param url: URL of git repository from which topology definition is downloaded
    :param rev: Revision of git repository
    :return: Hardware usage or None if error occurs.
    """
    try:
        top_def = definitions.get_definition(url, rev, settings.CRCZP_CONFIG)
        client = utils.get_terraform_client()
        client.validate_topology_definition(top_def)
        top_instance = client.get_topology_instance(top_def)
    except (
        exceptions.GitError,
        exceptions.ImproperlyConfigured,
        exceptions.ValidationError,
        CrczpException,
    ):
        return None

    return client.get_hardware_usage(top_instance)


def get_hardware_usage_of_sandbox(pool: Pool) -> HardwareUsage | None:
    """
    Get Heat Stack hardware usage of a single sandbox in a pool, whether it is allocated or not.

    :param pool: Pool to get HardwareUsage from.
    :return: Hardware usage or None if error occurs.
    """
    # sentinel object is used to differentiate between stored None and cache miss
    sentinel = object()
    definition = pool.definition

    hardware_usage = cache.get(get_cache_key(pool), sentinel)
    if hardware_usage is sentinel:
        hardware_usage = _get_hardware_usage(definition.url, definition.rev)

    limits = cache.get(PROJECT_LIMITS_CACHE_IDENTIFIER, sentinel)
    if limits is sentinel:
        client = utils.get_terraform_client()
        limits = client.get_project_limits()

    hardware_usage_pool = hardware_usage
    if hardware_usage_pool:
        hardware_usage_pool *= pool.size
        hardware_usage_pool /= limits

    cache.set(get_cache_key(pool), hardware_usage, POOL_CACHE_TIMEOUT)
    cache.set(PROJECT_LIMITS_CACHE_IDENTIFIER, limits, POOL_CACHE_TIMEOUT)

    return hardware_usage_pool


def get_cache_key(pool: Pool) -> str:
    """
    Get unique key which is used as cache record key

    :param pool: Pool for which the cache record is for
    :return: Cache key as string
    """
    return POOL_CACHE_PREFIX.format(pool.id)
