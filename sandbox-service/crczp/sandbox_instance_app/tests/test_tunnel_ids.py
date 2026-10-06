"""Tests that tunnel ids can be derived from real stack names."""

import pytest

from crczp.openstack_driver.network_forwarding import MAX_TUNNELS_PER_SANDBOX, tunnel_id_base

pytestmark = pytest.mark.django_db


def test_tunnel_id_base_follows_sandbox_id(allocation_unit):
    """The stack name of an allocation unit yields the tunnel id block of its sandbox id."""
    base = tunnel_id_base(allocation_unit.get_stack_name())

    assert base == allocation_unit.id * MAX_TUNNELS_PER_SANDBOX
