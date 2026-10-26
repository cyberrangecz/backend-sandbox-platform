"""Tests for listing and deleting the OpenStack resources of a stack."""

from types import SimpleNamespace

import pytest
from neutronclient.common.exceptions import Conflict, NotFound
from novaclient.exceptions import ClientException as NovaClientException
from novaclient.exceptions import NotFound as NovaNotFound
from pytest_mock import MockerFixture

from crczp.cloud_commons import (
    CloudResource,
    DeletionOutcome,
    StackNameScheme,
    TransformationConfiguration,
)
from crczp.openstack_driver import open_stack_proxy as proxy_module
from crczp.openstack_driver.open_stack_proxy import OpenStackProxy

STACK_NAMES = StackNameScheme('default0')
STACK = 'default0-p0000000001-s0000000001'
LONGER_STACK = 'default0-p0000000001-s0000000012'
OTHER_STACK = 'default0-p0000000002-s0000000001'
FOREIGN_STACK = 'other-p0000000001-s0000000001'


@pytest.fixture(name='cloud')
def fixture_cloud(mocker: MockerFixture):
    """A mock parent holding the nova and neutron clients, recording call order."""
    cloud = mocker.MagicMock()
    cloud.nova.servers.list.return_value = [
        SimpleNamespace(id='srv-1', name=f'{STACK}-server', flavor={'id': 'f-small'}),
        SimpleNamespace(id='srv-2', name=f'{STACK}-man', flavor={'id': 'f-large'}),
        SimpleNamespace(id='srv-3', name=f'{OTHER_STACK}-server', flavor={'id': 'f-large'}),
        SimpleNamespace(id='srv-4', name='base-server', flavor={'id': 'f-large'}),
        SimpleNamespace(id='srv-5', name=f'{FOREIGN_STACK}-server', flavor={'id': 'f-large'}),
    ]
    cloud.nova.flavors.list.return_value = [
        SimpleNamespace(id='f-small', ram=2000),
        SimpleNamespace(id='f-large', ram=4000),
    ]
    cloud.nova.servers.get.side_effect = NovaNotFound(404)
    cloud.neutron.list_networks.return_value = {
        'networks': [
            {'id': 'net-1', 'name': f'{STACK}-switch', 'tags': [STACK]},
            {'id': 'net-2', 'name': f'{LONGER_STACK}-switch', 'tags': []},
            {'id': 'net-base', 'name': 'base-net', 'tags': []},
        ]
    }
    cloud.neutron.list_subnets.return_value = {
        'subnets': [{'id': 'sub-1', 'name': f'{STACK}-switch-subnet', 'tags': []}]
    }
    cloud.neutron.list_ports.side_effect = lambda **filters: {
        'ports': [
            {'id': 'iface-1', 'device_owner': 'network:router_interface'},
            {'id': 'iface-2', 'device_owner': 'network:router_interface'},
        ]
        if filters.get('device_id') == 'rtr-1'
        else [
            {'id': 'port-1', 'name': f'{STACK}-link-1', 'tags': [STACK], 'device_owner': ''},
            {'id': 'port-2', 'name': '', 'tags': [OTHER_STACK], 'device_owner': ''},
        ]
    }
    cloud.neutron.list_routers.return_value = {
        'routers': [{'id': 'rtr-1', 'name': f'{STACK}-tapm-rtr', 'tags': [STACK]}]
    }
    cloud.neutron.list_floatingips.return_value = {
        'floatingips': [{'id': 'fip-1', 'floating_ip_address': '10.0.0.5', 'tags': [STACK]}]
    }
    cloud.neutron.list.return_value = {'tap_mirrors': [{'id': 'tm-1', 'name': f'{STACK}-tapm-0'}]}
    return cloud


@pytest.fixture(name='proxy')
def fixture_proxy(cloud, trc: TransformationConfiguration) -> OpenStackProxy:
    """An OpenStackProxy over the mock clients."""
    return OpenStackProxy(
        cloud.glance, cloud.nova, cloud.neutron, 'url', 'app_id', 'app_secret', trc
    )


def _by_id(results) -> dict:
    return {result.resource.resource_id: result for result in results}


def test_list_reports_each_stack_of_this_deployment_with_its_server_ram(proxy) -> None:
    inventories = proxy.list_stack_inventories(STACK_NAMES)

    assert set(inventories) == {STACK, LONGER_STACK, OTHER_STACK}
    stack = inventories[STACK]
    assert {resource.kind for resource in stack.resources} == {
        'server',
        'network',
        'subnet',
        'port',
        'router',
        'floating_ip',
        'tap_mirror',
    }
    assert stack.ram_gb == 6.0
    assert inventories[OTHER_STACK].ram_gb == 4.0
    assert CloudResource('floating_ip', 'fip-1', '10.0.0.5') in stack.resources


def test_list_without_tap_mirror_extension_still_lists_the_rest(cloud, proxy) -> None:
    cloud.neutron.list.side_effect = NotFound()

    inventories = proxy.list_stack_inventories(STACK_NAMES)

    assert all(
        resource.kind != 'tap_mirror'
        for inventory in inventories.values()
        for resource in inventory.resources
    )


def test_delete_runs_in_dependency_order_and_spares_other_stacks(cloud, proxy) -> None:
    results = proxy.delete_stack_resources(STACK, STACK_NAMES)

    assert [result.resource.kind for result in results] == [
        'tap_mirror',
        'floating_ip',
        'server',
        'server',
        'router',
        'port',
        'subnet',
        'network',
    ]
    assert all(result.outcome is DeletionOutcome.DELETED for result in results)
    deleted_ids = [
        call.args[0]
        for call in cloud.method_calls
        if call[0].startswith(('nova.servers.delete', 'neutron.delete_'))
    ]
    assert deleted_ids == ['fip-1', 'srv-1', 'srv-2', 'rtr-1', 'port-1', 'sub-1', 'net-1']
    cloud.neutron.delete.assert_called_once_with('/taas/tap_mirrors/tm-1')


@pytest.mark.parametrize(
    'stack_name', ['', 'default0', 'default0-p0000000001', 'base', FOREIGN_STACK]
)
def test_delete_refuses_what_is_not_a_stack_name_of_this_deployment(
    cloud, proxy, stack_name: str
) -> None:
    with pytest.raises(ValueError, match='Not a stack name'):
        proxy.delete_stack_resources(stack_name, STACK_NAMES)

    cloud.nova.servers.list.assert_not_called()
    cloud.nova.servers.delete.assert_not_called()


def test_delete_removes_router_interfaces_and_gateway_before_the_router(cloud, proxy) -> None:
    proxy.delete_stack_resources(STACK, STACK_NAMES)

    router_calls = [
        call[0]
        for call in cloud.method_calls
        if call[0]
        in {
            'neutron.remove_interface_router',
            'neutron.update_router',
            'neutron.delete_router',
            'neutron.delete_port',
        }
    ]
    assert router_calls == [
        'neutron.remove_interface_router',
        'neutron.remove_interface_router',
        'neutron.update_router',
        'neutron.delete_router',
        'neutron.delete_port',
    ]


def test_delete_goes_on_with_the_router_when_an_interface_is_already_gone(cloud, proxy) -> None:
    cloud.neutron.remove_interface_router.side_effect = [NotFound(), None]

    results = _by_id(proxy.delete_stack_resources(STACK, STACK_NAMES))

    assert cloud.neutron.remove_interface_router.call_count == 2
    cloud.neutron.delete_router.assert_called_once_with('rtr-1')
    assert results['rtr-1'].outcome is DeletionOutcome.DELETED


def test_delete_reports_a_router_gone_only_when_the_router_itself_is_missing(cloud, proxy) -> None:
    cloud.neutron.delete_router.side_effect = NotFound()

    results = _by_id(proxy.delete_stack_resources(STACK, STACK_NAMES))

    assert results['rtr-1'].outcome is DeletionOutcome.ALREADY_GONE


def test_delete_waits_for_servers_before_deleting_ports(cloud, proxy) -> None:
    proxy.delete_stack_resources(STACK, STACK_NAMES)

    names = [call[0] for call in cloud.method_calls]
    assert names.index('nova.servers.get') < names.index('neutron.delete_port')
    assert {call.args[0] for call in cloud.nova.servers.get.call_args_list} == {'srv-1', 'srv-2'}


def test_delete_tells_a_missing_resource_apart(cloud, proxy) -> None:
    cloud.neutron.delete_port.side_effect = NotFound()

    results = _by_id(proxy.delete_stack_resources(STACK, STACK_NAMES))

    assert results['port-1'].outcome is DeletionOutcome.ALREADY_GONE
    assert results['port-1'].error == ''


def test_delete_reports_a_failure_and_carries_on(cloud, proxy) -> None:
    cloud.neutron.delete_subnet.side_effect = Conflict('subnet in use')

    results = _by_id(proxy.delete_stack_resources(STACK, STACK_NAMES))

    assert results['sub-1'].outcome is DeletionOutcome.FAILED
    assert 'subnet in use' in results['sub-1'].error
    assert results['net-1'].outcome is DeletionOutcome.DELETED


def test_delete_reports_a_server_that_never_disappears(cloud, proxy, mocker: MockerFixture) -> None:
    cloud.nova.servers.get.side_effect = None
    mocker.patch.object(proxy_module.time, 'sleep')
    mocker.patch.object(
        proxy_module.time,
        'monotonic',
        side_effect=[0, 1, proxy_module.SERVER_DELETION_TIMEOUT_SECONDS + 1],
    )

    results = _by_id(proxy.delete_stack_resources(STACK, STACK_NAMES))

    assert results['srv-1'].outcome is DeletionOutcome.FAILED
    assert 'Still present' in results['srv-1'].error
    assert results['srv-2'].outcome is DeletionOutcome.FAILED
    assert results['port-1'].outcome is DeletionOutcome.DELETED


def test_delete_keeps_waiting_through_a_failed_server_lookup(
    cloud, proxy, mocker: MockerFixture
) -> None:
    cloud.nova.servers.get.side_effect = [
        NovaClientException(500),
        NovaNotFound(404),
        NovaNotFound(404),
    ]
    sleep = mocker.patch.object(proxy_module.time, 'sleep')

    results = _by_id(proxy.delete_stack_resources(STACK, STACK_NAMES))

    sleep.assert_called_once()
    assert results['srv-1'].outcome is DeletionOutcome.DELETED
    assert results['srv-2'].outcome is DeletionOutcome.DELETED
