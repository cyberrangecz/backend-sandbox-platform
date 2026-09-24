"""Tests for role-scoped credential lifecycle: cascade on sandbox deletion."""

import pytest
from django.db import IntegrityError

from crczp.sandbox_instance_app.models import SandboxNetbirdAccess, SandboxRoleKeypair

pytestmark = pytest.mark.django_db


class TestRoleCredentialCascade:
    """A credential cannot outlive its sandbox."""

    def test_deleting_sandbox_cascades_role_keypairs(self, sandbox):
        """Every SandboxRoleKeypair row for a deleted sandbox is removed with it."""
        SandboxRoleKeypair.objects.create(
            sandbox=sandbox, role='red-team', private_key='priv', public_key='pub'
        )
        SandboxRoleKeypair.objects.create(
            sandbox=sandbox, role='blue-team', private_key='priv', public_key='pub'
        )

        sandbox.delete()

        assert not SandboxRoleKeypair.objects.filter(role__in=['red-team', 'blue-team']).exists()

    def test_deleting_sandbox_cascades_every_netbird_access_row(self, sandbox):
        """Every SandboxNetbirdAccess row (flat and per-role) is removed with the sandbox."""
        SandboxNetbirdAccess.objects.create(sandbox=sandbox, access_setup_key_value='flat')
        SandboxNetbirdAccess.objects.create(
            sandbox=sandbox, role='red-team', access_setup_key_value='red'
        )
        sandbox_id = sandbox.id

        sandbox.delete()

        assert not SandboxNetbirdAccess.objects.filter(sandbox_id=sandbox_id).exists()

    def test_role_keypair_unique_together_sandbox_and_role(self, sandbox):
        """A sandbox holds at most one keypair per role."""
        SandboxRoleKeypair.objects.create(
            sandbox=sandbox, role='red-team', private_key='priv', public_key='pub'
        )
        with pytest.raises(IntegrityError):
            SandboxRoleKeypair.objects.create(
                sandbox=sandbox, role='red-team', private_key='priv2', public_key='pub2'
            )

    def test_netbird_access_unique_together_sandbox_and_role(self, sandbox):
        """A sandbox holds at most one access row per role, including the flat one."""
        SandboxNetbirdAccess.objects.create(sandbox=sandbox, role='red-team')
        with pytest.raises(IntegrityError):
            SandboxNetbirdAccess.objects.create(sandbox=sandbox, role='red-team')
