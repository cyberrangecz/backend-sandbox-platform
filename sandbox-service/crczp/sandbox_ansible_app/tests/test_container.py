"""Tests for the Ansible container wrappers."""

from crczp.sandbox_ansible_app.lib.container import DockerContainer


def test_docker_outputs_rejoin_lines_split_across_chunks(mocker):
    """Test that lines and UTF-8 characters split across log chunks are stored whole."""
    docker_container = mocker.MagicMock()
    docker_container.logs.return_value = [b'line one\n\xc4', b'\x8daj\npart', b'ial']
    mocker.patch.object(DockerContainer, '_run_container', return_value=docker_container)
    store_line = mocker.patch.object(DockerContainer, '_store_line')
    container = DockerContainer(
        'url', 'rev', mocker.MagicMock(), 'ssh', 'inventory', 'containers', 'credentials'
    )

    container.get_container_outputs()

    assert [call.args[0] for call in store_line.call_args_list] == ['line one', 'čaj', 'partial']
