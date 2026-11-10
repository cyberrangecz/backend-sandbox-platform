"""Tests for listing and deleting the OpenStack resources of a stack."""

import json

import pytest
import requests
from openstack import exceptions as sdk_exceptions
from openstack.compute.v2.flavor import Flavor
from openstack.compute.v2.server import Server
from openstack.network.v2.floating_ip import FloatingIP
from openstack.network.v2.network import Network
from openstack.network.v2.port import Port
from openstack.network.v2.router import Router
from openstack.network.v2.subnet import Subnet
from openstack.network.v2.tap_mirror import TapMirror
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
NEUTRON_ENDPOINT = 'http://neutron-internal.test:9696/v2.0'


def _api_error(status: int, body: dict, path: str) -> sdk_exceptions.HttpException:
    """The SDK exception for an error answer of an internal endpoint, as the SDK parses it."""
    response = requests.Response()
    response.status_code = status
    response.url = f'{NEUTRON_ENDPOINT}{path}'
    response.headers.update({
        'Content-Type': 'application/json',
        'x-openstack-request-id': 'req-0d9e8f7a-6b5c-4d3e-2f1a-0b9c8d7e6f5a',
    })
    response._content = json.dumps(body).encode()  # pylint: disable=protected-access
    try:
        sdk_exceptions.raise_from_response(response)
    except sdk_exceptions.HttpException as error:
        return error
    raise AssertionError(f'HTTP {status} did not raise')


def _neutron_not_found(kind: str, resource_id: str) -> sdk_exceptions.HttpException:
    message = f'{kind.capitalize()} {resource_id} could not be found.'
    body = {'NeutronError': {'type': f'{kind}NotFound', 'message': message, 'detail': ''}}
    return _api_error(404, body, f'/{kind}s/{resource_id}')


@pytest.fixture(name='cloud')
def fixture_cloud(fake_conn):
    """The fake connection, holding the servers, flavors and Neutron resources of a project."""
    compute, network = fake_conn.compute, fake_conn.network
    compute.servers.return_value = [
        Server.existing(id='srv-1', name=f'{STACK}-server', flavor={'id': 'f-small'}),
        Server.existing(id='srv-2', name=f'{STACK}-man', flavor={'id': 'f-large'}),
        Server.existing(id='srv-3', name=f'{OTHER_STACK}-server', flavor={'id': 'f-large'}),
        Server.existing(id='srv-4', name='base-server', flavor={'id': 'f-large'}),
        Server.existing(id='srv-5', name=f'{FOREIGN_STACK}-server', flavor={'id': 'f-large'}),
    ]
    compute.flavors.return_value = [
        Flavor.existing(id='f-small', ram=2000),
        Flavor.existing(id='f-large', ram=4000),
    ]
    compute.get_server.side_effect = sdk_exceptions.NotFoundException('Server gone')
    network.networks.return_value = [
        Network.existing(id='net-1', name=f'{STACK}-switch', tags=[STACK]),
        Network.existing(id='net-2', name=f'{LONGER_STACK}-switch', tags=[]),
        Network.existing(id='net-base', name='base-net', tags=[]),
    ]
    network.subnets.return_value = [
        Subnet.existing(id='sub-1', name=f'{STACK}-switch-subnet', tags=[])
    ]
    network.ports.side_effect = lambda **query: (
        [
            Port.existing(id='iface-1', device_owner='network:router_interface'),
            Port.existing(id='iface-2', device_owner='network:router_interface'),
        ]
        if query.get('device_id') == 'rtr-1'
        else [
            Port.existing(id='port-1', name=f'{STACK}-link-1', tags=[STACK], device_owner=''),
            Port.existing(id='port-2', name='', tags=[OTHER_STACK], device_owner=''),
        ]
    )
    network.routers.return_value = [
        Router.existing(id='rtr-1', name=f'{STACK}-tapm-rtr', tags=[STACK])
    ]
    network.ips.return_value = [
        FloatingIP.existing(id='fip-1', floating_ip_address='10.0.0.5', tags=[STACK])
    ]
    network.tap_mirrors.return_value = [TapMirror.existing(id='tm-1', name=f'{STACK}-tapm-0')]
    return fake_conn


@pytest.fixture(name='proxy')
def fixture_proxy(cloud, trc: TransformationConfiguration) -> OpenStackProxy:
    """An OpenStackProxy over the fake connection."""
    return OpenStackProxy(cloud, 'url', 'app_id', 'app_secret', trc)


@pytest.fixture(name='calls')
def fixture_calls(mocker: MockerFixture, cloud):
    """Records the calls to the compute and network proxies in one ordered list."""
    manager = mocker.Mock()
    manager.attach_mock(cloud.compute, 'compute')
    manager.attach_mock(cloud.network, 'network')
    return manager.mock_calls


def _by_id(results) -> dict:
    return {result.resource.resource_id: result for result in results}


def test_list_reports_each_stack_of_this_deployment_with_its_server_ram(cloud, proxy) -> None:
    """Each stack of this deployment is listed with its servers' RAM; other stacks are left out."""
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
    # Like Nova's flavor list for the project, without the private flavors of other projects.
    cloud.compute.flavors.assert_called_once_with(is_public=True)


def test_list_without_tap_mirror_extension_still_lists_the_rest(cloud, proxy) -> None:
    """A cloud without the TaaS extension has no tap mirrors, and everything else is listed."""
    cloud.network.tap_mirrors.side_effect = sdk_exceptions.NotFoundException('No TaaS')

    inventories = proxy.list_stack_inventories(STACK_NAMES)

    assert all(
        resource.kind != 'tap_mirror'
        for inventory in inventories.values()
        for resource in inventory.resources
    )
    assert STACK in inventories


def test_delete_runs_in_dependency_order_and_spares_other_stacks(proxy, calls) -> None:
    """The stack's resources are deleted dependents first; no other stack's resource is touched."""
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
    deletions = [call for call in calls if call[0].split('.')[1].startswith('delete_')]
    assert [(name, args) for name, args, _ in deletions] == [
        ('network.delete_tap_mirror', ('tm-1',)),
        ('network.delete_ip', ('fip-1',)),
        ('compute.delete_server', ('srv-1',)),
        ('compute.delete_server', ('srv-2',)),
        ('network.delete_router', ('rtr-1',)),
        ('network.delete_port', ('port-1',)),
        ('network.delete_subnet', ('sub-1',)),
        ('network.delete_network', ('net-1',)),
    ]
    # The SDK would ignore a missing resource; every deletion has to report it instead.
    assert all(kwargs == {'ignore_missing': False} for _, _, kwargs in deletions)


@pytest.mark.parametrize(
    'stack_name', ['', 'default0', 'default0-p0000000001', 'base', FOREIGN_STACK]
)
def test_delete_refuses_what_is_not_a_stack_name_of_this_deployment(
    cloud, proxy, stack_name: str
) -> None:
    """Nothing is listed or deleted for a name that is not a stack of this deployment."""
    with pytest.raises(ValueError, match='Not a stack name'):
        proxy.delete_stack_resources(stack_name, STACK_NAMES)

    cloud.compute.servers.assert_not_called()
    cloud.compute.delete_server.assert_not_called()


def test_delete_removes_router_interfaces_and_gateway_before_the_router(
    cloud, proxy, calls
) -> None:
    """A router's interfaces and gateway are removed first, then the router, then the ports."""
    proxy.delete_stack_resources(STACK, STACK_NAMES)

    router_calls = [
        call
        for call in calls
        if call[0]
        in {
            'network.remove_interface_from_router',
            'network.update_router',
            'network.delete_router',
            'network.delete_port',
        }
    ]
    assert router_calls == [
        ('network.remove_interface_from_router', ('rtr-1',), {'port': 'iface-1'}),
        ('network.remove_interface_from_router', ('rtr-1',), {'port': 'iface-2'}),
        ('network.update_router', ('rtr-1',), {'external_gateway_info': {}}),
        ('network.delete_router', ('rtr-1',), {'ignore_missing': False}),
        ('network.delete_port', ('port-1',), {'ignore_missing': False}),
    ]
    cloud.network.ports.assert_any_call(device_id='rtr-1')


def test_delete_goes_on_with_the_router_when_an_interface_is_already_gone(cloud, proxy) -> None:
    """An interface that is already gone does not stop the deletion of its router."""
    cloud.network.remove_interface_from_router.side_effect = [
        _neutron_not_found('port', 'iface-1'),
        None,
    ]

    results = _by_id(proxy.delete_stack_resources(STACK, STACK_NAMES))

    assert cloud.network.remove_interface_from_router.call_count == 2
    cloud.network.delete_router.assert_called_once_with('rtr-1', ignore_missing=False)
    assert results['rtr-1'].outcome is DeletionOutcome.DELETED


def test_delete_reports_a_router_gone_only_when_the_router_itself_is_missing(cloud, proxy) -> None:
    """A router that no longer exists is reported as already gone."""
    cloud.network.delete_router.side_effect = _neutron_not_found('router', 'rtr-1')

    results = _by_id(proxy.delete_stack_resources(STACK, STACK_NAMES))

    assert results['rtr-1'].outcome is DeletionOutcome.ALREADY_GONE


def test_delete_waits_for_servers_before_deleting_ports(cloud, proxy, calls) -> None:
    """The servers' disappearance is awaited before their ports are deleted."""
    proxy.delete_stack_resources(STACK, STACK_NAMES)

    names = [call[0] for call in calls]
    assert names.index('compute.get_server') < names.index('network.delete_port')
    assert {call.args[0] for call in cloud.compute.get_server.call_args_list} == {
        'srv-1',
        'srv-2',
    }


def test_delete_tells_a_missing_resource_apart(cloud, proxy) -> None:
    """A resource that no longer exists is reported as already gone, without an error."""
    cloud.network.delete_port.side_effect = _neutron_not_found('port', 'port-1')

    results = _by_id(proxy.delete_stack_resources(STACK, STACK_NAMES))

    assert results['port-1'].outcome is DeletionOutcome.ALREADY_GONE
    assert results['port-1'].error == ''


def test_delete_reports_a_failure_with_neutrons_message_and_carries_on(cloud, proxy) -> None:
    """A failed deletion is reported with Neutron's message, and the next resources are deleted."""
    message = (
        'Unable to complete operation on subnet sub-1: One or more ports have an IP '
        'allocation from this subnet.'
    )
    cloud.network.delete_subnet.side_effect = _api_error(
        409,
        {'NeutronError': {'type': 'SubnetInUse', 'message': message, 'detail': ''}},
        '/subnets/sub-1',
    )

    results = _by_id(proxy.delete_stack_resources(STACK, STACK_NAMES))

    assert results['sub-1'].outcome is DeletionOutcome.FAILED
    # The failure reaches the stage error message the API returns: no internal endpoint URL.
    assert results['sub-1'].error == (
        f'{message} (HTTP 409) (Request-ID: req-0d9e8f7a-6b5c-4d3e-2f1a-0b9c8d7e6f5a)'
    )
    assert results['net-1'].outcome is DeletionOutcome.DELETED


def test_delete_reports_a_server_that_never_disappears(cloud, proxy, mocker: MockerFixture) -> None:
    """A server still present when the wait ends counts as failed; its ports are deleted anyway."""
    cloud.compute.get_server.side_effect = None
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
    """A failed lookup of a deleted server is logged, and the wait goes on."""
    cloud.compute.get_server.side_effect = [
        _api_error(500, {'computeFault': {'code': 500, 'message': 'Unexpected'}}, '/'),
        sdk_exceptions.NotFoundException('Server gone'),
        sdk_exceptions.NotFoundException('Server gone'),
    ]
    sleep = mocker.patch.object(proxy_module.time, 'sleep')

    results = _by_id(proxy.delete_stack_resources(STACK, STACK_NAMES))

    sleep.assert_called_once()
    assert results['srv-1'].outcome is DeletionOutcome.DELETED
    assert results['srv-2'].outcome is DeletionOutcome.DELETED
