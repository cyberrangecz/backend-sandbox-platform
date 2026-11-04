"""Tests for resource lookups in CrczpTerraformClientManager."""

import pickle  # noqa: S403
import subprocess  # noqa: S404
from unittest.mock import MagicMock, patch

import pytest
from crczp.cloud_commons import CrczpException, StackNameScheme

from crczp.terraform_driver.terraform_client_elements import CrczpTerraformBackendType
from crczp.terraform_driver.terraform_client_manager import CrczpTerraformClientManager
from crczp.terraform_driver.terraform_exceptions import TerraformStateLocked, TerraformUnlockFailed

STACK_NAMES = StackNameScheme('default0')
STACK_A = 'default0-p0000000001-s0000000001'
STACK_B = 'default0-p0000000001-s0000000002'
FOREIGN_STACK = 'other-p0000000001-s0000000001'


def _resource(type_: str, name: str, resource_id: str) -> dict:
    return {
        'mode': 'managed',
        'type': type_,
        'name': name,
        'instances': [{'attributes': {'id': resource_id}}],
    }


@pytest.fixture(name='manager')
def manager_fixture(tmp_path) -> CrczpTerraformClientManager:
    """A manager whose cloud client, configuration and backend are mocks."""
    return CrczpTerraformClientManager(str(tmp_path), MagicMock(), MagicMock(), None, MagicMock())


def test_get_resource_id_ignores_non_instance_with_node_name(manager) -> None:
    """A later non-instance resource with the node's name does not shadow the instance."""
    state = [
        _resource('openstack_compute_instance_v2', 'stack-tapm-0', 'instance-id'),
        _resource('openstack_taas_tap_mirror_v2', 'stack-tapm-0', 'tap-mirror-id'),
    ]
    with patch.object(manager, 'list_stack_resources', return_value=state):
        assert manager.get_resource_id('stack', 'tapm-0') == 'instance-id'


def test_get_resource_id_finds_aws_instance(manager) -> None:
    """AWS instances are resolved the same way as OpenStack ones."""
    state = [
        _resource('aws_instance', 'stack-server', 'i-0abc'),
        _resource('aws_network_interface', 'stack-server', 'eni-1'),
    ]
    with patch.object(manager, 'list_stack_resources', return_value=state):
        assert manager.get_resource_id('stack', 'server') == 'i-0abc'


def _port(type_: str, name: str, mac: str, ip: str) -> dict:
    return {
        'mode': 'managed',
        'type': type_,
        'name': name,
        'instances': [{'attributes': {'mac_address': mac, 'ip': ip}}],
    }


@pytest.mark.parametrize(
    ('port_type', 'instance_type', 'association_type'),
    [
        (
            'openstack_networking_port_v2',
            'openstack_compute_instance_v2',
            'openstack_networking_floatingip_associate_v2',
        ),
        ('aws_network_interface', 'aws_instance', 'aws_eip_association'),
    ],
)
def test_get_enriched_topology_instance_ignores_non_port_with_port_name(
    manager, port_type, instance_type, association_type
) -> None:
    """Port attributes come from the port even when a later resource shares its name."""
    manager.trc.man_out_port = 'man-out'
    manager.cloud_client.get_private_ip.side_effect = lambda attrs: attrs['ip']
    link = MagicMock()
    link.name = 'link-1'
    topology = MagicMock()
    topology.get_links.return_value = [link]
    state = [
        _port(port_type, 'stack-man-out', 'mac-man', '10.0.0.1'),
        _port(port_type, 'stack-link-1', 'mac-1', '10.0.0.2'),
        _resource(instance_type, 'stack-man-out', 'instance-id'),
        _resource(association_type, 'stack-link-1', 'assoc-id'),
    ]
    with patch.object(manager, 'list_stack_resources', return_value=state):
        result = manager.get_enriched_topology_instance('stack', topology)
    assert result.ip == '10.0.0.1'
    assert link.ip == '10.0.0.2'
    assert link.mac == 'mac-1'


LOCK_ERROR_OUTPUT = """
Error: Error acquiring the state lock

Error message: resource temporarily unavailable
Lock Info:
  ID:        3f2a9c1e-5b7d-4e8a-9c0d-1a2b3c4d5e6f
  Path:      terraform.tfstate
  Operation: OperationTypeApply
"""


def _finished_process(stdout: str, stderr: str, return_code: int) -> MagicMock:
    process = MagicMock()
    process.communicate.return_value = (stdout, stderr)
    process.returncode = return_code
    return process


def test_wait_for_destroy_reports_lock_id(manager) -> None:
    """A destroy that failed on a held state lock raises with the lock id."""
    process = _finished_process(LOCK_ERROR_OUTPUT, '', 1)
    with pytest.raises(TerraformStateLocked) as raised:
        manager.wait_for_destroy(process)
    assert raised.value.lock_id == '3f2a9c1e-5b7d-4e8a-9c0d-1a2b3c4d5e6f'


def test_wait_for_destroy_reads_lock_id_from_stderr(manager) -> None:
    """The lock id is found when the error went to stderr."""
    process = _finished_process('', LOCK_ERROR_OUTPUT, 1)
    with pytest.raises(TerraformStateLocked):
        manager.wait_for_destroy(process)


def test_wait_for_destroy_passes_other_failure_through(manager) -> None:
    """A failure without a lock is returned to the caller unchanged."""
    process = _finished_process('Error: quota exceeded', '', 1)
    assert manager.wait_for_destroy(process) == ('Error: quota exceeded', '', 1)


def test_wait_for_destroy_ignores_lock_text_on_success(manager) -> None:
    """A successful destroy never raises, whatever its output mentions."""
    process = _finished_process(LOCK_ERROR_OUTPUT, '', 0)
    assert manager.wait_for_destroy(process)[2] == 0


def test_force_unlock_stack_runs_unlock_in_selected_workspace(manager) -> None:
    """The unlock runs after init and workspace selection, with the id as one list item."""
    calls = MagicMock()
    process = _finished_process('', '', 0)
    with (
        patch.object(manager, '_initialize_stack_dir', calls.initialize),
        patch.object(manager, '_switch_terraform_workspace', calls.switch),
        patch.object(manager, '_execute_command', return_value=process) as execute,
    ):
        manager.force_unlock_stack('stack', 'abc-123')
    calls.initialize.assert_called_once_with('stack')
    calls.switch.assert_called_once_with('stack', manager.get_stack_dir('stack'))
    assert execute.call_args.args[0] == ['tofu', 'force-unlock', '-force', '-no-color', 'abc-123']
    assert execute.call_args.kwargs['cwd'] == manager.get_stack_dir('stack')


def test_force_unlock_stack_raises_when_command_fails(manager) -> None:
    """A failing unlock command raises TerraformUnlockFailed."""
    process = _finished_process('', 'Failed to unlock', 1)
    with (
        patch.object(manager, '_initialize_stack_dir'),
        patch.object(manager, '_switch_terraform_workspace'),
        patch.object(manager, '_execute_command', return_value=process),
        pytest.raises(TerraformUnlockFailed),
    ):
        manager.force_unlock_stack('stack', 'abc-123')


@pytest.mark.parametrize('lock_id', ['', '-force', '; rm -rf /', 'a b'])
def test_force_unlock_stack_rejects_unsafe_lock_id(manager, lock_id) -> None:
    """A lock id that is not a plain identifier never reaches the command."""
    with (
        patch.object(manager, '_execute_command') as execute,
        pytest.raises(ValueError),
    ):
        manager.force_unlock_stack('stack', lock_id)
    execute.assert_not_called()


def test_list_workspaces_local_reads_stack_directories(tmp_path) -> None:
    """With the local backend the workspaces are the ones kept in each stack directory."""
    backend = MagicMock(backend_type=CrczpTerraformBackendType.LOCAL)
    manager = CrczpTerraformClientManager(str(tmp_path), MagicMock(), MagicMock(), None, backend)
    (tmp_path / STACK_B / 'terraform.tfstate.d' / STACK_B).mkdir(parents=True)
    (tmp_path / STACK_A / 'terraform.tfstate.d' / STACK_A).mkdir(parents=True)
    (tmp_path / FOREIGN_STACK / 'terraform.tfstate.d' / FOREIGN_STACK).mkdir(parents=True)
    (tmp_path / 'stack-without-workspace').mkdir()
    assert manager.list_workspaces(STACK_NAMES) == [STACK_A, STACK_B]


def test_list_workspaces_logs_a_prefixed_name_without_ids_and_leaves_it_alone(
    tmp_path, caplog
) -> None:
    """A workspace with the deployment prefix but no pool or unit id is an error, not a stack."""
    backend = MagicMock(backend_type=CrczpTerraformBackendType.LOCAL)
    manager = CrczpTerraformClientManager(str(tmp_path), MagicMock(), MagicMock(), None, backend)
    malformed = f'{STACK_NAMES.prefix}-orphan'
    (tmp_path / malformed / 'terraform.tfstate.d' / malformed).mkdir(parents=True)
    (tmp_path / STACK_A / 'terraform.tfstate.d' / STACK_A).mkdir(parents=True)

    with caplog.at_level('ERROR'):
        assert manager.list_workspaces(STACK_NAMES) == [STACK_A]

    assert malformed in caplog.text


def test_list_workspaces_remote_keeps_the_stacks_of_this_deployment(tmp_path) -> None:
    """With a remote backend the names come from `tofu workspace list`, other names left out."""
    backend = MagicMock(backend_type=CrczpTerraformBackendType.POSTGRES)
    manager = CrczpTerraformClientManager(str(tmp_path), MagicMock(), MagicMock(), None, backend)
    process = _finished_process(
        f'  default\n* {STACK_A}\n  {STACK_B}\n  {FOREIGN_STACK}\n  default0-p0000000001\n\n',
        '',
        0,
    )
    with (
        patch.object(manager, 'init_terraform') as init,
        patch.object(manager, '_create_terraform_backend_file'),
        patch.object(manager, '_execute_command', return_value=process) as execute,
    ):
        assert manager.list_workspaces(STACK_NAMES) == [STACK_A, STACK_B]
    init.assert_called_once()
    assert execute.call_args.args[0] == ['tofu', 'workspace', 'list', '-no-color']


def test_list_workspaces_remote_raises_when_command_fails(tmp_path) -> None:
    """A failing listing raises CrczpException."""
    backend = MagicMock(backend_type=CrczpTerraformBackendType.KUBERNETES)
    manager = CrczpTerraformClientManager(str(tmp_path), MagicMock(), MagicMock(), None, backend)
    process = _finished_process('', 'backend unreachable', 1)
    with (
        patch.object(manager, 'init_terraform'),
        patch.object(manager, '_create_terraform_backend_file'),
        patch.object(manager, '_execute_command', return_value=process),
        pytest.raises(CrczpException),
    ):
        manager.list_workspaces(STACK_NAMES)


@pytest.mark.parametrize(
    ('force', 'expected_command'),
    [
        (False, ['tofu', 'workspace', 'delete', 'stack']),
        (True, ['tofu', 'workspace', 'delete', '-force', 'stack']),
    ],
    ids=['plain', 'forced'],
)
def test_delete_terraform_workspace_forces_only_on_request(
    manager, force, expected_command
) -> None:
    """The force flag is passed to the delete command only when requested."""
    process = _finished_process('', '', 0)
    with (
        patch.object(manager, '_switch_terraform_workspace'),
        patch.object(manager, '_execute_command', return_value=process) as execute,
    ):
        manager.delete_terraform_workspace('stack', force=force)
    assert execute.call_args.args[0] == expected_command


def test_delete_terraform_workspace_raises_when_command_fails(manager) -> None:
    """A failing delete command raises."""
    process = _finished_process('', 'Workspace is not empty', 1)
    with (
        patch.object(manager, '_switch_terraform_workspace'),
        patch.object(manager, '_execute_command', return_value=process),
        pytest.raises(CrczpException),
    ):
        manager.delete_terraform_workspace('stack', force=True)


def test_wait_for_destroy_kills_a_process_that_outlives_the_timeout(manager) -> None:
    """A destroy past its timeout is killed and reaped before the failure is raised."""
    process = MagicMock()
    process.communicate.side_effect = [subprocess.TimeoutExpired('tofu', 1), ('', '')]
    with pytest.raises(CrczpException, match='did not finish'):
        manager.wait_for_destroy(process, 1)
    process.kill.assert_called_once_with()
    assert process.communicate.call_count == 2


def test_a_state_lock_error_keeps_its_lock_id_through_pickling():
    restored = pickle.loads(pickle.dumps(TerraformStateLocked('lock-1')))  # noqa: S301

    assert restored.lock_id == 'lock-1'
    assert str(restored) == str(TerraformStateLocked('lock-1'))
