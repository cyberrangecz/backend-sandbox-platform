"""Docker and Kubernetes container wrappers for executing Ansible playbooks."""

import abc
import codecs
import time
from dataclasses import dataclass
from typing import Any, override

import docker
import structlog
import urllib3
from django.conf import settings
from docker.models.containers import Container
from kubernetes import client, config, watch
from kubernetes.client import V1JobStatus, V1PersistentVolumeClaimVolumeSource

from crczp.cloud_commons import CrczpException
from crczp.sandbox_ansible_app.models import (
    AllocationAnsibleOutput,
    AnsibleStage,
    CleanupAnsibleOutput,
)
from crczp.sandbox_common_lib import exceptions

LOG = structlog.get_logger()
ANSIBLE_FILE_VOLUME_NAME = 'ansible-files-path'


@dataclass
class ContainerVolume:
    """Volume binding configuration for a container."""

    name: str
    bind: str
    mode: str


class BaseContainer(abc.ABC):  # pylint: disable=too-many-instance-attributes
    """Base class for all containers."""

    ANSIBLE_SSH_DIR = ContainerVolume(name='ansible-ssh-dir', bind='/root/.ssh', mode='rw')
    ANSIBLE_INVENTORY_PATH = ContainerVolume(
        name='ansible-inventory-path', bind='/app/inventory.yml', mode='ro'
    )
    ANSIBLE_DOCKER_CONTAINER_PATH = ContainerVolume(
        name='docker-containers-path', bind='/root/containers', mode='rw'
    )
    GIT_CREDENTIALS_PATH = ContainerVolume(
        name='git-credentials-path', bind='/app/.git-credentials', mode='ro'
    )

    @abc.abstractmethod
    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        url: str,
        rev: str,
        stage: AnsibleStage,
        ssh_directory: str,
        inventory_path: str,
        containers_path: str,
        credentials_path: str,
        cleanup: bool = False,
    ) -> None:
        """Initialize the container."""
        self.url = url
        self.rev = rev
        self.stage = stage
        self.cleanup = cleanup
        self.ssh_directory = ssh_directory
        self.inventory_path = inventory_path
        self.containers_path = containers_path
        self.credentials_path = credentials_path
        self.output_class = CleanupAnsibleOutput if self.cleanup else AllocationAnsibleOutput
        self.stage_info = (
            {'cleanup_stage': self.stage} if self.cleanup else {'allocation_stage': self.stage}
        )

    def _store_line(self, line: str) -> None:
        """Store one line of the container output as the stage's next output row."""
        self.output_class.objects.create(**self.stage_info, content=line.removesuffix('\r'))

    @abc.abstractmethod
    def _run_container(self) -> Any:
        """Run the container."""

    @abc.abstractmethod
    def get_container_name(self) -> str:
        """Get the container ID."""

    @abc.abstractmethod
    def get_container_outputs(self) -> None:
        """Get the container outputs."""

    @abc.abstractmethod
    def check_container_status(self) -> None:
        """Check the container status."""

    @abc.abstractmethod
    def delete(self) -> None:
        """Delete the container."""

    @classmethod
    @abc.abstractmethod
    def delete_container(cls, container_name: str) -> None:
        """Delete the container. Used when cancelling a stage."""


class DockerContainer(BaseContainer):
    """Docker container."""

    DOCKER_NETWORK = settings.CRCZP_CONFIG.ansible_docker_network
    CLIENT = docker.from_env

    @override
    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        url: str,
        rev: str,
        stage: AnsibleStage,
        ssh_directory: str,
        inventory_path: str,
        containers_path: str,
        credentials_path: str,
        cleanup: bool = False,
    ) -> None:
        """Initialize the container."""
        super().__init__(
            url,
            rev,
            stage,
            ssh_directory,
            inventory_path,
            containers_path,
            credentials_path,
            cleanup,
        )
        self.container = self._run_container()

    @override
    def _run_container(self) -> Container:
        """
        Run Ansible in Docker container.
        """
        volumes = {
            self.ssh_directory: self.ANSIBLE_SSH_DIR.__dict__,
            self.inventory_path: self.ANSIBLE_INVENTORY_PATH.__dict__,
            self.containers_path: self.ANSIBLE_DOCKER_CONTAINER_PATH.__dict__,
            self.credentials_path: self.GIT_CREDENTIALS_PATH.__dict__,
        }
        command = [
            '-u',
            self.url,
            '-r',
            self.rev,
            '-i',
            self.ANSIBLE_INVENTORY_PATH.bind,
            '-a',
            settings.CRCZP_CONFIG.answers_storage_api,
        ]
        command += ['-c'] if self.cleanup else []
        LOG.debug('Ansible container options', command=command)
        return self.CLIENT().containers.run(
            settings.CRCZP_CONFIG.ansible_docker_image,
            detach=True,
            command=command,
            volumes=volumes,
            network=self.DOCKER_NETWORK,
        )

    @override
    def get_container_name(self) -> str:
        """Get the container ID."""
        return str(self.container.id)

    @override
    def get_container_outputs(self) -> None:
        """Store the container output as it is written, one row per line."""
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        pending = ''
        for chunk in self.container.logs(stream=True, follow=True):
            *lines, pending = (pending + decoder.decode(chunk)).split('\n')
            for line in lines:
                self._store_line(line)
        pending += decoder.decode(b'', final=True)
        if pending:
            self._store_line(pending)

    @override
    def check_container_status(self) -> None:
        """Check the container status."""
        status = self.container.wait(timeout=settings.CRCZP_CONFIG.sandbox_ansible_timeout)
        status_code = status['StatusCode']
        if status_code != 0:
            raise exceptions.AnsibleError(
                f'Ansible stage {self.stage.id} failed. See Ansible outputs for details.'
            )

    @classmethod
    def _get_container(cls, container_id: str) -> Container:
        """
        Return Docker container with given container ID.
        """
        return cls.CLIENT().containers.get(container_id)

    @classmethod
    @override
    def delete_container(cls, container_name: str) -> None:
        """Delete the container. Used when cancelling a stage."""
        cls._get_container(container_name).remove(force=True)

    @override
    def delete(self) -> None:
        """Delete the container."""
        self.delete_container(self.get_container_name())


class KubernetesContainer(BaseContainer):
    """Kubernetes container."""

    ALLOCATION_JOB_NAME = 'ansible-allocation-{}'
    CLEANUP_JOB_NAME = 'ansible-cleanup-{}'
    KUBERNETES_NAMESPACE = settings.CRCZP_CONFIG.ansible_runner_settings.namespace
    CORE_API = client.CoreV1Api()
    BATCH_API = client.BatchV1Api()
    FINISHED_POD_PHASES = ('Succeeded', 'Failed')
    POD_LOG_REOPEN_DELAY_SECONDS = 1
    POD_LOG_READ_TIMEOUT_SECONDS = 300

    @override
    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        url: str,
        rev: str,
        stage: AnsibleStage,
        ssh_directory: str,
        inventory_path: str,
        containers_path: str,
        credentials_path: str,
        cleanup: bool = False,
    ) -> None:
        """Initialize the container."""
        super().__init__(
            url,
            rev,
            stage,
            ssh_directory,
            inventory_path,
            containers_path,
            credentials_path,
            cleanup,
        )
        self.job_name = (
            self.ALLOCATION_JOB_NAME.format(self.stage.id)
            if not self.cleanup
            else self.CLEANUP_JOB_NAME.format(self.stage.id)
        )
        self._initialize_kube_config()
        self.container = self._run_container()

    @classmethod
    def _initialize_kube_config(cls) -> None:
        """Initialize the kubernetes config."""
        try:
            config.load_incluster_config()
        except config.ConfigException as exc:
            raise CrczpException(exc) from exc

        cls.CORE_API = client.CoreV1Api()
        cls.BATCH_API = client.BatchV1Api()

    def _create_container(self) -> client.V1Container:
        """Create the container."""
        kuber_container = client.V1Container(
            name=self.job_name,
            image=settings.CRCZP_CONFIG.ansible_docker_image,
            args=[
                '-u',
                self.url,
                '-r',
                self.rev,
                '-i',
                self.ANSIBLE_INVENTORY_PATH.bind,
                '-a',
                settings.CRCZP_CONFIG.answers_storage_api,
            ],
        )
        kuber_container.args += ['-c'] if self.cleanup else []

        # Add environment variable for Git SSL verification
        kuber_container.env = [
            client.V1EnvVar(
                name='GIT_SSL_NO_VERIFY',
                value='true'
                if getattr(settings.CRCZP_CONFIG, 'git_skip_ssl_verification', False)
                else 'false',
            )
        ]

        kuber_container.volume_mounts = [
            client.V1VolumeMount(
                name=ANSIBLE_FILE_VOLUME_NAME,
                mount_path=self.ANSIBLE_SSH_DIR.bind,
                sub_path=self.ssh_directory[
                    len(settings.CRCZP_CONFIG.ansible_runner_settings.volumes_path) + 1 :
                ],
            ),
            client.V1VolumeMount(
                name=ANSIBLE_FILE_VOLUME_NAME,
                mount_path=self.ANSIBLE_INVENTORY_PATH.bind,
                sub_path=self.inventory_path[
                    len(settings.CRCZP_CONFIG.ansible_runner_settings.volumes_path) + 1 :
                ],
            ),
            client.V1VolumeMount(
                name=ANSIBLE_FILE_VOLUME_NAME,
                mount_path=self.ANSIBLE_DOCKER_CONTAINER_PATH.bind,
                sub_path=self.containers_path[
                    len(settings.CRCZP_CONFIG.ansible_runner_settings.volumes_path) + 1 :
                ],
            ),
            client.V1VolumeMount(
                name=ANSIBLE_FILE_VOLUME_NAME,
                mount_path=self.GIT_CREDENTIALS_PATH.bind,
                sub_path=self.credentials_path[
                    len(settings.CRCZP_CONFIG.ansible_runner_settings.volumes_path) + 1 :
                ],
            ),
        ]

        return kuber_container

    def _create_kube_job(self) -> Any:
        """
        Run Ansible in Kubernetes job.
        """
        kuber_container = self._create_container()
        pvc_name = settings.CRCZP_CONFIG.ansible_runner_settings.persistent_volume_claim_name

        job = client.V1Job(
            metadata=client.V1ObjectMeta(name=self.job_name, namespace=self.KUBERNETES_NAMESPACE),
            spec=client.V1JobSpec(
                backoff_limit=0,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(
                        name=self.job_name, namespace=self.KUBERNETES_NAMESPACE
                    ),
                    spec=client.V1PodSpec(
                        restart_policy='Never',
                        containers=[kuber_container],
                        volumes=[
                            client.V1Volume(
                                name=ANSIBLE_FILE_VOLUME_NAME,
                                persistent_volume_claim=V1PersistentVolumeClaimVolumeSource(
                                    claim_name=pvc_name,
                                ),
                            )
                        ],
                    ),
                ),
            ),
        )
        self.BATCH_API.create_namespaced_job(namespace=self.KUBERNETES_NAMESPACE, body=job)

        return job

    @override
    def _run_container(self) -> Any:
        """Run the container."""
        return self._create_kube_job()

    @override
    def get_container_name(self) -> str:
        """Return the container name."""
        return self.job_name

    def _wait_for_pod_start(self) -> str | None:
        """
        Wait for pod to start.
        """
        w = watch.Watch()
        for event in w.stream(
            self.CORE_API.list_namespaced_pod,
            namespace=self.KUBERNETES_NAMESPACE,
            label_selector=f'job-name={self.job_name}',
        ):
            pod_phase = event['object'].status.phase
            if pod_phase in ('Running', *self.FINISHED_POD_PHASES):
                w.stop()
                return str(event['object'].metadata.name)
            if event['type'] == 'DELETED':
                w.stop()
                raise CrczpException('Pod was deleted before it was ready.')
        return None

    def _wait_for_job_finish(self) -> V1JobStatus | None:
        """
        Wait for job to finish.
        """
        w = watch.Watch()
        for event in w.stream(
            self.BATCH_API.list_namespaced_job,
            namespace=self.KUBERNETES_NAMESPACE,
            label_selector=f'job-name={self.job_name}',
        ):
            job_status = event['object'].status
            if job_status.failed or job_status.succeeded:
                w.stop()
                return job_status
        return None

    def _pod_finished(self, pod_name: str) -> bool:
        """Whether the pod's container has exited."""
        pod = self.CORE_API.read_namespaced_pod(name=pod_name, namespace=self.KUBERNETES_NAMESPACE)
        return pod.status.phase in self.FINISHED_POD_PHASES

    def _follow_pod_log(self, pod_name: str, stored: int) -> int:
        """
        Follow the pod log from its first line until the stream closes, breaks or stays
        silent past the read timeout, storing each complete line past the ones already stored.

        :param pod_name: Name of the pod whose log is followed.
        :param stored: Count of leading log lines already stored.
        :return: Count of leading log lines stored once the stream ends.
        """
        seen = 0
        try:
            for line in watch.Watch().stream(
                self.CORE_API.read_namespaced_pod_log,
                name=pod_name,
                namespace=self.KUBERNETES_NAMESPACE,
                _request_timeout=(None, self.POD_LOG_READ_TIMEOUT_SECONDS),
            ):
                seen += 1
                if seen > stored:
                    self._store_line(line)
        except urllib3.exceptions.HTTPError as exc:
            LOG.warning('Pod log stream ended early', pod_name=pod_name, error=str(exc))
        return max(seen, stored)

    def _store_remaining_pod_log(self, pod_name: str, stored: int) -> None:
        """
        Store the lines of the finished pod's whole log past the ones already stored,
        its last line included when no line break ends it.
        """
        pod_log = self.CORE_API.read_namespaced_pod_log(
            name=pod_name, namespace=self.KUBERNETES_NAMESPACE, _preload_content=False
        ).data.decode('utf-8', errors='replace')
        lines = pod_log.split('\n')
        if lines[-1] == '':
            lines.pop()
        for line in lines[stored:]:
            self._store_line(line)

    @override
    def get_container_outputs(self) -> None:
        """
        Store the pod output as it is written, one row per line, each line once.
        """
        pod_name = self._wait_for_pod_start()
        if pod_name is None:
            raise exceptions.AnsibleError('Pod did not start in time.')
        stored = self._follow_pod_log(pod_name, 0)
        while not self._pod_finished(pod_name):
            time.sleep(self.POD_LOG_REOPEN_DELAY_SECONDS)
            stored = self._follow_pod_log(pod_name, stored)
        self._store_remaining_pod_log(pod_name, stored)

    @override
    def check_container_status(self) -> None:
        """Check the container status."""
        status = self._wait_for_job_finish()
        if status is None:
            raise exceptions.AnsibleError(
                f'Ansible stage {self.stage.id} did not finish: job watch stream exhausted.'
            )
        if status.failed or (status.conditions and status.conditions[0].type == 'Failed'):
            raise exceptions.AnsibleError(
                f'Ansible stage {self.stage.id} failed. See Ansible outputs for details.'
            )

    @classmethod
    @override
    def delete_container(cls, container_name: str) -> None:
        """Delete the container."""
        cls._initialize_kube_config()
        try:
            cls.BATCH_API.delete_namespaced_job(
                name=container_name,
                namespace=cls.KUBERNETES_NAMESPACE,
                body=client.V1DeleteOptions(propagation_policy='Background'),
            )
        except client.ApiException as exc:
            raise CrczpException(f'Failed to delete job: {exc}') from exc

    @override
    def delete(self) -> None:
        """Delete the container."""
        self.delete_container(self.get_container_name())
