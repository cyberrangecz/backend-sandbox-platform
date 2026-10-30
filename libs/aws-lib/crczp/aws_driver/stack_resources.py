"""Listing and deletion of the AWS resources of a stack through the EC2 API."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from botocore.exceptions import ClientError, WaiterError

from crczp.cloud_commons import (
    CloudResource,
    DeletionOutcome,
    ResourceDeletion,
    ResourceKind,
    StackInventory,
    StackName,
    StackNameScheme,
    TaggedResource,
    delete_in_order,
    group_by_stack,
    select_stack,
)

STACK_TAG_KEY = 'crczp-stack'
TRAFFIC_MIRROR_SESSION = 'traffic_mirror_session'
TRAFFIC_MIRROR_TARGET = 'traffic_mirror_target'
TRAFFIC_MIRROR_FILTER = 'traffic_mirror_filter'
INSTANCE = 'instance'
NETWORK_INTERFACE = 'network_interface'
SECURITY_GROUP = 'security_group'
SUBNET = 'subnet'
VPC = 'vpc'
INSTANCE_TERMINATION_WAITER_DELAY_SECONDS = 5
INSTANCE_TERMINATION_WAITER_MAX_ATTEMPTS = 60
MEMORY_MIB_PER_GIB = 1024


def _is_not_found(error: Exception) -> bool:
    """Whether an EC2 error says the resource does not exist."""
    if not isinstance(error, ClientError):
        return False
    return str(error.response.get('Error', {}).get('Code', '')).endswith('NotFound')


def _tagged(kind: str, item: dict[str, Any], id_key: str) -> TaggedResource:
    """The resource an EC2 description holds, with its stack tag when it carries one."""
    tags = {tag['Key']: tag['Value'] for tag in item.get('Tags') or item.get('TagSet') or []}
    stack_tag = tags.get(STACK_TAG_KEY)
    return TaggedResource(
        CloudResource(kind, item[id_key], tags.get('Name', '')),
        (stack_tag,) if stack_tag else (),
    )


@dataclass(frozen=True)
class _AccountResources:
    """The resources of the account with their stack tags, and the descriptions read again."""

    tagged: list[TaggedResource]
    instances: list[dict[str, Any]]
    security_groups_by_id: dict[str, dict[str, Any]]


class AwsStackResources:
    """
    Finds the resources of stacks by their stack tag, their Name tag, their VPC or their
    attached instance, and deletes them.

    :param ec2_client: boto3 EC2 client of the account the stacks live in
    """

    def __init__(self, ec2_client: Any) -> None:
        self.ec2_client = ec2_client

    def list_stack_inventories(self, stack_names: StackNameScheme) -> dict[str, StackInventory]:
        """
        List the stacks of one deployment that have resources in the account.

        :param stack_names: The stack names of the deployment
        :return: Resources and the RAM in GB held by the instances, keyed by full stack name
        """
        account = self._discover(stack_names)
        return group_by_stack(
            account.tagged, self._get_instance_ram_gb(account.instances), stack_names
        )

    def delete_stack_resources(
        self, stack_name: str, stack_names: StackNameScheme
    ) -> list[ResourceDeletion]:
        """
        Delete the resources of one stack, dependents first. The rules of its security groups
        are removed beforehand; a group whose rules could not be removed and that then fails
        to be deleted reports both errors.

        :param stack_name: The full stack name
        :param stack_names: The stack names of the deployment
        :return: The result of deleting each resource, in deletion order
        :raise ValueError: The name is not a full stack name of the deployment
        """
        stack_names.require(stack_name)
        account = self._discover(stack_names)
        resources = select_stack(account.tagged, stack_name, stack_names)
        revoke_errors = self._revoke_security_group_rules(
            account.security_groups_by_id[resource.resource_id]
            for resource in resources
            if resource.kind == SECURITY_GROUP
        )
        return [
            _with_revoke_error(result, revoke_errors)
            for result in delete_in_order(resources, self._resource_kinds(), _is_not_found)
        ]

    def _resource_kinds(self) -> tuple[ResourceKind, ...]:
        """The kinds of stack resources, dependents first, with how each is deleted."""
        client = self.ec2_client
        return (
            ResourceKind(
                TRAFFIC_MIRROR_SESSION,
                lambda resource_id: client.delete_traffic_mirror_session(
                    TrafficMirrorSessionId=resource_id
                ),
            ),
            ResourceKind(
                TRAFFIC_MIRROR_TARGET,
                lambda resource_id: client.delete_traffic_mirror_target(
                    TrafficMirrorTargetId=resource_id
                ),
            ),
            ResourceKind(
                TRAFFIC_MIRROR_FILTER,
                lambda resource_id: client.delete_traffic_mirror_filter(
                    TrafficMirrorFilterId=resource_id
                ),
            ),
            ResourceKind(
                INSTANCE,
                lambda resource_id: client.terminate_instances(InstanceIds=[resource_id]),
                self._await_instances_terminated,
            ),
            ResourceKind(
                NETWORK_INTERFACE,
                lambda resource_id: client.delete_network_interface(NetworkInterfaceId=resource_id),
            ),
            ResourceKind(
                SECURITY_GROUP,
                lambda resource_id: client.delete_security_group(GroupId=resource_id),
            ),
            ResourceKind(SUBNET, lambda resource_id: client.delete_subnet(SubnetId=resource_id)),
            ResourceKind(VPC, lambda resource_id: client.delete_vpc(VpcId=resource_id)),
        )

    def _describe(self, operation: str, result_key: str) -> list[dict[str, Any]]:
        pages = self.ec2_client.get_paginator(operation).paginate()
        return [item for page in pages for item in page[result_key]]

    def _discover(self, stack_names: StackNameScheme) -> _AccountResources:
        """
        Describe the resources of the account. A network interface, subnet or security group
        that belongs to no stack by itself belongs to the stack of its VPC; a network interface
        outside any stack's VPC belongs to the stack of the instance it is attached to.
        """
        instances = [
            instance
            for reservation in self._describe('describe_instances', 'Reservations')
            for instance in reservation['Instances']
            if instance['State']['Name'] != 'terminated'
        ]
        tagged_instances = [_tagged(INSTANCE, instance, 'InstanceId') for instance in instances]
        tagged_vpcs = [
            _tagged(VPC, vpc, 'VpcId') for vpc in self._describe('describe_vpcs', 'Vpcs')
        ]
        stack_by_instance_id = _stack_by_resource_id(tagged_instances, stack_names)
        stack_by_vpc_id = _stack_by_resource_id(tagged_vpcs, stack_names)
        security_groups = [
            group
            for group in self._describe('describe_security_groups', 'SecurityGroups')
            if group['GroupName'] != 'default'
        ]
        tagged_resources = [
            *tagged_instances,
            *tagged_vpcs,
            *(
                _with_fallback(
                    _tagged(NETWORK_INTERFACE, interface, 'NetworkInterfaceId'),
                    stack_by_vpc_id.get(interface.get('VpcId', ''))
                    or stack_by_instance_id.get(
                        interface.get('Attachment', {}).get('InstanceId', '')
                    ),
                    stack_names,
                )
                for interface in self._describe('describe_network_interfaces', 'NetworkInterfaces')
            ),
            *(
                _with_fallback(
                    _tagged(SUBNET, subnet, 'SubnetId'),
                    stack_by_vpc_id.get(subnet.get('VpcId', '')),
                    stack_names,
                )
                for subnet in self._describe('describe_subnets', 'Subnets')
            ),
            *(
                _with_fallback(
                    _tagged(SECURITY_GROUP, group, 'GroupId'),
                    stack_by_vpc_id.get(group.get('VpcId', '')),
                    stack_names,
                )
                for group in security_groups
            ),
            *(
                _tagged(TRAFFIC_MIRROR_TARGET, target, 'TrafficMirrorTargetId')
                for target in self._describe(
                    'describe_traffic_mirror_targets', 'TrafficMirrorTargets'
                )
            ),
            *(
                _tagged(TRAFFIC_MIRROR_FILTER, mirror_filter, 'TrafficMirrorFilterId')
                for mirror_filter in self._describe(
                    'describe_traffic_mirror_filters', 'TrafficMirrorFilters'
                )
            ),
            *(
                _tagged(TRAFFIC_MIRROR_SESSION, session, 'TrafficMirrorSessionId')
                for session in self._describe(
                    'describe_traffic_mirror_sessions', 'TrafficMirrorSessions'
                )
            ),
        ]
        return _AccountResources(
            tagged_resources,
            instances,
            {group['GroupId']: group for group in security_groups},
        )

    def _get_instance_ram_gb(self, instances: list[dict[str, Any]]) -> dict[str, float]:
        instance_types = sorted({instance['InstanceType'] for instance in instances})
        if not instance_types:
            return {}
        described = self.ec2_client.describe_instance_types(InstanceTypes=instance_types)
        ram_gb_by_type = {
            instance_type['InstanceType']: round(
                int(instance_type['MemoryInfo']['SizeInMiB']) / MEMORY_MIB_PER_GIB, 1
            )
            for instance_type in described['InstanceTypes']
        }
        return {
            instance['InstanceId']: ram_gb_by_type.get(instance['InstanceType'], 0.0)
            for instance in instances
        }

    def _revoke_security_group_rules(self, groups: Iterable[dict[str, Any]]) -> dict[str, str]:
        """
        Remove the rules of each security group, so that groups referencing each other can
        be deleted.

        :return: The error per ID of a group whose rules could not be removed
        """
        errors = {}
        for group in groups:
            try:
                if group.get('IpPermissions'):
                    self.ec2_client.revoke_security_group_ingress(
                        GroupId=group['GroupId'], IpPermissions=group['IpPermissions']
                    )
                if group.get('IpPermissionsEgress'):
                    self.ec2_client.revoke_security_group_egress(
                        GroupId=group['GroupId'], IpPermissions=group['IpPermissionsEgress']
                    )
            except ClientError as error:
                if not _is_not_found(error):
                    errors[group['GroupId']] = str(error)
        return errors

    def _await_instances_terminated(self, instances: Sequence[CloudResource]) -> dict[str, str]:
        """
        Wait until the instances are terminated.

        :return: An error per ID of an instance still not terminated when the wait ends
        """
        instance_ids = [instance.resource_id for instance in instances]
        try:
            self.ec2_client.get_waiter('instance_terminated').wait(
                InstanceIds=instance_ids,
                WaiterConfig={
                    'Delay': INSTANCE_TERMINATION_WAITER_DELAY_SECONDS,
                    'MaxAttempts': INSTANCE_TERMINATION_WAITER_MAX_ATTEMPTS,
                },
            )
        except WaiterError as error:
            reservations = self.ec2_client.describe_instances(
                Filters=[{'Name': 'instance-id', 'Values': instance_ids}]
            )['Reservations']
            return {
                instance['InstanceId']: f'Not terminated: {error}'
                for reservation in reservations
                for instance in reservation['Instances']
                if instance['State']['Name'] != 'terminated'
            }
        return {}


def _stack_by_resource_id(
    tagged_resources: Iterable[TaggedResource], stack_names: StackNameScheme
) -> dict[str, StackName]:
    """The stack of each resource that belongs to one, keyed by resource ID."""
    stacks = {
        tagged.resource.resource_id: stack_names.owner_of_resource(tagged)
        for tagged in tagged_resources
    }
    return {resource_id: stack for resource_id, stack in stacks.items() if stack is not None}


def _with_fallback(
    tagged: TaggedResource, fallback: StackName | None, stack_names: StackNameScheme
) -> TaggedResource:
    """Tag a resource that belongs to no stack by itself with the fallback stack, if any."""
    if fallback is None or stack_names.owner_of_resource(tagged) is not None:
        return tagged
    return TaggedResource(tagged.resource, (*tagged.tags, str(fallback)))


def _with_revoke_error(
    result: ResourceDeletion, revoke_errors: Mapping[str, str]
) -> ResourceDeletion:
    """Add the rule removal error of a security group that failed to be deleted."""
    revoke_error = revoke_errors.get(result.resource.resource_id)
    if revoke_error is None or result.outcome is not DeletionOutcome.FAILED:
        return result
    return replace(result, error=f'Rule removal failed: {revoke_error}; {result.error}')
