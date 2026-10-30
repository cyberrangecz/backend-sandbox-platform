"""Tests for the Ansible container wrappers."""

import pytest
import urllib3

from crczp.cloud_commons import CrczpException
from crczp.sandbox_ansible_app.lib.container import DockerContainer, KubernetesContainer


def test_docker_outputs_rejoin_lines_split_across_chunks(mocker):
    """Test that lines and UTF-8 characters split across log chunks are stored whole."""
    docker_container = mocker.MagicMock()
    docker_container.logs.return_value = [b'line one\n\xc4', b'\x8daj\npart', b'ial']
    mocker.patch.object(DockerContainer, '_run_container', return_value=docker_container)
    store_line = mocker.patch.object(DockerContainer, '_store_line')
    container = DockerContainer(
        'url',
        'rev',
        mocker.MagicMock(),
        'container-name',
        'ssh',
        'inventory',
        'containers',
        'credentials',
    )

    container.get_container_outputs()

    assert [call.args[0] for call in store_line.call_args_list] == ['line one', 'čaj', 'partial']


def test_docker_container_runs_under_the_given_name(mocker):
    """Test that the Docker container is created under the name it is given."""
    docker_client = mocker.MagicMock()
    mocker.patch.object(DockerContainer, 'CLIENT', return_value=docker_client)

    container = DockerContainer(
        'url',
        'rev',
        mocker.MagicMock(),
        'ansible-allocation-7-0fb3160d',
        'ssh',
        'inventory',
        'containers',
        'credentials',
    )

    assert docker_client.containers.run.call_args.kwargs['name'] == 'ansible-allocation-7-0fb3160d'
    assert container.get_container_name() == 'ansible-allocation-7-0fb3160d'


@pytest.mark.parametrize(
    'transport_error',
    [
        urllib3.exceptions.MaxRetryError(urllib3.connectionpool.ConnectionPool('host'), '/job'),
        urllib3.exceptions.ProtocolError('connection aborted'),
    ],
)
def test_kubernetes_job_deletion_reports_an_unreachable_cluster_as_a_platform_error(
    mocker, transport_error
):
    """A cluster that does not answer surfaces as CrczpException, which cancellation tolerates."""
    mocker.patch.object(KubernetesContainer, '_initialize_kube_config')
    batch_api = mocker.patch.object(KubernetesContainer, 'BATCH_API', create=True)
    batch_api.delete_namespaced_job.side_effect = transport_error

    with pytest.raises(CrczpException):
        KubernetesContainer.delete_container('job-name')
