"""Tests for listing and deleting the AWS resources of a stack."""

from typing import Any

import pytest
from botocore.exceptions import ClientError, WaiterError
from pytest_mock import MockerFixture

from crczp.aws_driver.stack_resources import STACK_TAG_KEY, AwsStackResources
from crczp.cloud_commons import DeletionOutcome, StackNameScheme

STACK_NAMES = StackNameScheme('default0')
STACK = 'default0-p0000000001-s0000000001'
LONGER_STACK = 'default0-p0000000001-s0000000012'
FOREIGN_STACK = 'other-p0000000001-s0000000001'


def _tags(**tags: str) -> list[dict[str, str]]:
    return [{'Key': key, 'Value': value} for key, value in tags.items()]


def _client_error(code: str) -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': code}}, 'Operation')


def _instance(instance_id: str, instance_type: str, state: str, name: str) -> dict[str, Any]:
    return {
        'InstanceId': instance_id,
        'InstanceType': instance_type,
        'State': {'Name': state},
        'Tags': _tags(Name=name),
    }


@pytest.fixture(name='pages')
def fixture_pages() -> dict[str, list[dict[str, Any]]]:
    """
    Describe pages per EC2 operation: two stacks, one of which extends the other's name, a
    stack of another deployment, and network interfaces created before stack tagging.
    """
    return {
        'describe_instances': [
            {
                'Reservations': [
                    {
                        'Instances': [
                            _instance('i-1', 't3.small', 'running', f'{STACK}-server'),
                            _instance('i-2', 't3.large', 'running', f'{LONGER_STACK}-server'),
                            _instance('i-gone', 't3.large', 'terminated', f'{STACK}-old'),
                            _instance('i-foreign', 't3.large', 'running', f'{FOREIGN_STACK}-a'),
                        ]
                    }
                ]
            }
        ],
        'describe_network_interfaces': [
            {
                'NetworkInterfaces': [
                    {
                        'NetworkInterfaceId': 'eni-tagged',
                        'VpcId': 'vpc-1',
                        'TagSet': _tags(**{STACK_TAG_KEY: STACK}),
                    },
                    {'NetworkInterfaceId': 'eni-untagged', 'VpcId': 'vpc-1', 'TagSet': []},
                    {
                        'NetworkInterfaceId': 'eni-man-out',
                        'VpcId': 'vpc-base',
                        'Attachment': {'InstanceId': 'i-1'},
                        'TagSet': [],
                    },
                    {'NetworkInterfaceId': 'eni-base', 'VpcId': 'vpc-base', 'TagSet': []},
                ]
            }
        ],
        'describe_subnets': [
            {
                'Subnets': [
                    {
                        'SubnetId': 'sn-1',
                        'VpcId': 'vpc-1',
                        'Tags': _tags(Name=f'{STACK}-net-subnet'),
                    },
                    {'SubnetId': 'sn-base', 'VpcId': 'vpc-base', 'Tags': _tags(Name='base')},
                ]
            }
        ],
        'describe_vpcs': [
            {
                'Vpcs': [
                    {'VpcId': 'vpc-1', 'Tags': _tags(Name=f'{STACK}-net')},
                    {'VpcId': 'vpc-2', 'Tags': _tags(Name=f'{LONGER_STACK}-net')},
                    {'VpcId': 'vpc-foreign', 'Tags': _tags(Name=f'{FOREIGN_STACK}-net')},
                    {'VpcId': 'vpc-base', 'Tags': _tags(Name='Base Net')},
                ]
            }
        ],
        'describe_security_groups': [
            {
                'SecurityGroups': [
                    {
                        'GroupId': 'sg-1',
                        'GroupName': 'sandbox-internal-sg',
                        'VpcId': 'vpc-1',
                        'IpPermissions': [{'IpProtocol': '-1'}],
                        'IpPermissionsEgress': [{'IpProtocol': '-1'}],
                    },
                    {
                        'GroupId': 'sg-3',
                        'GroupName': 'sandbox-man-sg',
                        'VpcId': 'vpc-1',
                        'IpPermissions': [{'IpProtocol': 'tcp'}],
                        'IpPermissionsEgress': [],
                    },
                    {'GroupId': 'sg-2', 'GroupName': 'sandbox-man-sg', 'VpcId': 'vpc-2'},
                    {'GroupId': 'sg-default', 'GroupName': 'default', 'VpcId': 'vpc-1'},
                    {'GroupId': 'sg-base', 'GroupName': 'base-sg', 'VpcId': 'vpc-base'},
                ]
            }
        ],
        'describe_traffic_mirror_targets': [
            {
                'TrafficMirrorTargets': [
                    {'TrafficMirrorTargetId': 'tmt-1', 'Tags': _tags(Name=f'{STACK}-tmt-x')}
                ]
            }
        ],
        'describe_traffic_mirror_filters': [
            {
                'TrafficMirrorFilters': [
                    {'TrafficMirrorFilterId': 'tmf-1', 'Tags': _tags(Name=f'{STACK}-tmf')}
                ]
            }
        ],
        'describe_traffic_mirror_sessions': [
            {
                'TrafficMirrorSessions': [
                    {'TrafficMirrorSessionId': 'tms-1', 'Tags': _tags(Name=f'{STACK}-tms-0')}
                ]
            }
        ],
    }


@pytest.fixture(name='ec2')
def fixture_ec2(mocker: MockerFixture, pages: dict[str, list[dict[str, Any]]]):
    """A mock EC2 client serving the describe pages and recording every call in order."""
    ec2 = mocker.MagicMock()
    ec2.get_paginator.side_effect = lambda operation: mocker.MagicMock(
        paginate=mocker.MagicMock(return_value=pages[operation])
    )
    ec2.describe_instance_types.return_value = {
        'InstanceTypes': [
            {'InstanceType': 't3.small', 'MemoryInfo': {'SizeInMiB': 2048}},
            {'InstanceType': 't3.large', 'MemoryInfo': {'SizeInMiB': 8192}},
        ]
    }
    return ec2


@pytest.fixture(name='stack_resources')
def fixture_stack_resources(ec2) -> AwsStackResources:
    """The AWS stack resource handler over the mock EC2 client."""
    return AwsStackResources(ec2)


def _by_id(results) -> dict[str, Any]:
    return {result.resource.resource_id: result for result in results}


def test_list_reports_the_stacks_of_this_deployment_only(stack_resources) -> None:
    inventories = stack_resources.list_stack_inventories(STACK_NAMES)

    assert set(inventories) == {STACK, LONGER_STACK}
    stack = inventories[STACK]
    assert {(resource.kind, resource.resource_id) for resource in stack.resources} == {
        ('instance', 'i-1'),
        ('network_interface', 'eni-tagged'),
        ('network_interface', 'eni-untagged'),
        ('network_interface', 'eni-man-out'),
        ('subnet', 'sn-1'),
        ('vpc', 'vpc-1'),
        ('security_group', 'sg-1'),
        ('security_group', 'sg-3'),
        ('traffic_mirror_target', 'tmt-1'),
        ('traffic_mirror_filter', 'tmf-1'),
        ('traffic_mirror_session', 'tms-1'),
    }
    assert stack.ram_gb == 2.0
    assert inventories[LONGER_STACK].ram_gb == 8.0


def test_delete_runs_in_dependency_order(stack_resources, ec2) -> None:
    results = stack_resources.delete_stack_resources(STACK, STACK_NAMES)

    assert [result.resource.kind for result in results] == [
        'traffic_mirror_session',
        'traffic_mirror_target',
        'traffic_mirror_filter',
        'instance',
        'network_interface',
        'network_interface',
        'network_interface',
        'security_group',
        'security_group',
        'subnet',
        'vpc',
    ]
    assert all(result.outcome is DeletionOutcome.DELETED for result in results)
    deletion_calls = [
        call[0]
        for call in ec2.method_calls
        if call[0].startswith(('delete_', 'terminate_', 'revoke_', 'get_waiter'))
    ]
    assert deletion_calls == [
        'revoke_security_group_ingress',
        'revoke_security_group_egress',
        'revoke_security_group_ingress',
        'delete_traffic_mirror_session',
        'delete_traffic_mirror_target',
        'delete_traffic_mirror_filter',
        'terminate_instances',
        'get_waiter',
        'delete_network_interface',
        'delete_network_interface',
        'delete_network_interface',
        'delete_security_group',
        'delete_security_group',
        'delete_subnet',
        'delete_vpc',
    ]
    ec2.describe_security_groups.assert_not_called()


def test_delete_spares_the_stack_whose_name_extends_the_given_one(stack_resources, ec2) -> None:
    stack_resources.delete_stack_resources(STACK, STACK_NAMES)

    ec2.terminate_instances.assert_called_once_with(InstanceIds=['i-1'])
    ec2.delete_vpc.assert_called_once_with(VpcId='vpc-1')
    assert {call.kwargs['GroupId'] for call in ec2.delete_security_group.call_args_list} == {
        'sg-1',
        'sg-3',
    }


@pytest.mark.parametrize(
    'stack_name', ['', 'default0', 'default0-p0000000001', 'sandbox', FOREIGN_STACK]
)
def test_delete_refuses_what_is_not_a_stack_name_of_this_deployment(
    stack_resources, ec2, stack_name: str
) -> None:
    with pytest.raises(ValueError, match='Not a stack name'):
        stack_resources.delete_stack_resources(stack_name, STACK_NAMES)

    ec2.get_paginator.assert_not_called()
    ec2.terminate_instances.assert_not_called()


def test_delete_waits_for_instances_to_terminate(stack_resources, ec2) -> None:
    stack_resources.delete_stack_resources(STACK, STACK_NAMES)

    ec2.get_waiter.assert_called_once_with('instance_terminated')
    assert ec2.get_waiter.return_value.wait.call_args.kwargs['InstanceIds'] == ['i-1']


def test_delete_reports_an_instance_still_running_after_the_wait(stack_resources, ec2) -> None:
    ec2.get_waiter.return_value.wait.side_effect = WaiterError(
        'InstanceTerminated', 'Max attempts exceeded', {}
    )
    ec2.describe_instances.return_value = {
        'Reservations': [{'Instances': [{'InstanceId': 'i-1', 'State': {'Name': 'shutting-down'}}]}]
    }

    results = _by_id(stack_resources.delete_stack_resources(STACK, STACK_NAMES))

    assert results['i-1'].outcome is DeletionOutcome.FAILED
    assert 'Not terminated' in results['i-1'].error
    assert results['vpc-1'].outcome is DeletionOutcome.DELETED


def test_delete_tells_a_missing_resource_apart(stack_resources, ec2) -> None:
    ec2.delete_subnet.side_effect = _client_error('InvalidSubnetID.NotFound')

    results = _by_id(stack_resources.delete_stack_resources(STACK, STACK_NAMES))

    assert results['sn-1'].outcome is DeletionOutcome.ALREADY_GONE
    assert results['sn-1'].gone
    assert results['sn-1'].error == ''


def test_delete_reports_a_failure_and_carries_on(stack_resources, ec2) -> None:
    ec2.delete_subnet.side_effect = _client_error('DependencyViolation')

    results = _by_id(stack_resources.delete_stack_resources(STACK, STACK_NAMES))

    assert results['sn-1'].outcome is DeletionOutcome.FAILED
    assert 'DependencyViolation' in results['sn-1'].error
    assert results['vpc-1'].outcome is DeletionOutcome.DELETED


def test_delete_revokes_every_group_after_one_revocation_fails(stack_resources, ec2) -> None:
    ec2.revoke_security_group_ingress.side_effect = [_client_error('UnauthorizedOperation'), None]
    ec2.delete_security_group.side_effect = [_client_error('DependencyViolation'), None]

    results = _by_id(stack_resources.delete_stack_resources(STACK, STACK_NAMES))

    assert [
        call.kwargs['GroupId'] for call in ec2.revoke_security_group_ingress.call_args_list
    ] == ['sg-1', 'sg-3']
    assert results['sg-1'].outcome is DeletionOutcome.FAILED
    assert results['sg-1'].error.startswith('Rule removal failed: ')
    assert 'UnauthorizedOperation' in results['sg-1'].error
    assert 'DependencyViolation' in results['sg-1'].error
    assert results['sg-3'].outcome is DeletionOutcome.DELETED
