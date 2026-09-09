"""Tests for the EBS snapshot lookup of the AWS client."""

from collections.abc import Iterator

import botocore.session
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from crczp.aws_driver.aws_client import CrczpAwsClient

OWN = 'snap-0123456789abcdef0'
OTHER = 'snap-0123abcd'


@pytest.fixture(name='ec2')
def ec2_fixture(aws_client: CrczpAwsClient) -> Iterator[Stubber]:
    """Give the client a real EC2 client whose calls are answered by a Stubber."""
    aws_client.ec2_client = botocore.session.get_session().create_client(
        'ec2',
        region_name='eu-central-1',
        aws_access_key_id='key',
        aws_secret_access_key='secret',
    )
    with Stubber(aws_client.ec2_client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


def _expect_lookup(ec2: Stubber, snapshot_ids: list[str], sizes: dict[str, int]) -> None:
    ec2.add_response(
        'describe_snapshots',
        {'Snapshots': [{'SnapshotId': i, 'VolumeSize': s} for i, s in sizes.items()]},
        {'SnapshotIds': snapshot_ids, 'OwnerIds': ['self']},
    )


def _expect_error(ec2: Stubber, snapshot_ids: list[str], code: str) -> None:
    ec2.add_client_error(
        'describe_snapshots',
        service_error_code=code,
        expected_params={'SnapshotIds': snapshot_ids, 'OwnerIds': ['self']},
    )


def test_returns_sizes_of_own_snapshots(aws_client: CrczpAwsClient, ec2: Stubber) -> None:
    """The sizes of the snapshots the lookup returns are keyed by snapshot id."""
    _expect_lookup(ec2, [OWN, OTHER], {OWN: 20, OTHER: 8})

    assert aws_client.get_snapshot_sizes([OWN, OTHER]) == {OWN: 20, OTHER: 8}


def test_snapshot_not_returned_is_left_out(aws_client: CrczpAwsClient, ec2: Stubber) -> None:
    """A snapshot the lookup does not return, such as one of another account, is left out."""
    _expect_lookup(ec2, [OWN, OTHER], {OWN: 20})

    assert aws_client.get_snapshot_sizes([OWN, OTHER]) == {OWN: 20}


@pytest.mark.parametrize('code', ['InvalidSnapshot.NotFound', 'InvalidSnapshotID.Malformed'])
def test_unknown_snapshot_is_left_out(aws_client: CrczpAwsClient, ec2: Stubber, code: str) -> None:
    """One unknown ID fails the batch call, so the IDs are looked up one by one."""
    _expect_error(ec2, [OWN, OTHER], code)
    _expect_lookup(ec2, [OWN], {OWN: 20})
    _expect_error(ec2, [OTHER], code)

    assert aws_client.get_snapshot_sizes([OWN, OTHER]) == {OWN: 20}


def test_other_errors_are_raised(aws_client: CrczpAwsClient, ec2: Stubber) -> None:
    """Errors other than an unknown snapshot are not swallowed."""
    _expect_error(ec2, [OWN], 'UnauthorizedOperation')

    with pytest.raises(ClientError, match='UnauthorizedOperation'):
        aws_client.get_snapshot_sizes([OWN])


@pytest.mark.usefixtures('ec2')
def test_no_ids_skip_the_lookup(aws_client: CrczpAwsClient) -> None:
    """Without ids no request is sent; the Stubber fails on an unexpected call."""
    assert not aws_client.get_snapshot_sizes([])
