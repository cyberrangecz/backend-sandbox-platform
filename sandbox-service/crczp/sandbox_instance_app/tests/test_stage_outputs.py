"""Tests for the line break normalisation of stored stage output rows."""

# pylint: disable=redefined-outer-name

from typing import NamedTuple

import pytest
from django.db import models

from crczp.sandbox_ansible_app.lib.container import DockerContainer
from crczp.sandbox_ansible_app.models import AllocationAnsibleOutput, CleanupAnsibleOutput
from crczp.sandbox_instance_app.models import AllocationTerraformOutput, CleanupTerraformOutput

pytestmark = pytest.mark.django_db


class StageCase(NamedTuple):
    """One stage type with the fixtures and model storing its output rows."""

    request_fixture: str
    stage_fixture: str
    output_model: type[models.Model]
    stage_field: str


STAGE_CASES = {
    'terraform-allocation': StageCase(
        'allocation_request',
        'allocation_stage_stack',
        AllocationTerraformOutput,
        'allocation_stage',
    ),
    'networking-ansible-allocation': StageCase(
        'allocation_request',
        'allocation_stage_networking',
        AllocationAnsibleOutput,
        'allocation_stage',
    ),
    'user-ansible-allocation': StageCase(
        'allocation_request',
        'allocation_stage_user',
        AllocationAnsibleOutput,
        'allocation_stage',
    ),
    'terraform-cleanup': StageCase(
        'cleanup_request',
        'cleanup_stage_stack',
        CleanupTerraformOutput,
        'cleanup_stage',
    ),
    'networking-ansible-cleanup': StageCase(
        'cleanup_request',
        'cleanup_stage_networking',
        CleanupAnsibleOutput,
        'cleanup_stage',
    ),
    'user-ansible-cleanup': StageCase(
        'cleanup_request',
        'cleanup_stage_user',
        CleanupAnsibleOutput,
        'cleanup_stage',
    ),
}


@pytest.fixture(params=STAGE_CASES.values(), ids=list(STAGE_CASES))
def stage_case(request) -> StageCase:
    """Parametrize a test over every stage type."""
    return request.param


@pytest.fixture
def request_id(stage_case, request) -> int:
    """Return the id of the request the stage under test belongs to."""
    return request.getfixturevalue(stage_case.request_fixture).id


@pytest.fixture
def stage(stage_case, request_id, request):  # pylint: disable=unused-argument
    """Return the stage under test, created with its request."""
    return request.getfixturevalue(stage_case.stage_fixture)


class TestStageOutputLineBreaks:
    """Tests for the line break normalisation of stored output rows."""

    @pytest.mark.parametrize(
        ('written', 'stored'),
        [
            ('a\r\nb', 'a\nb'),
            ('progress 1%\rprogress 2%', 'progress 1%\nprogress 2%'),
            ('a\r\r\nb\n\rc', 'a\n\nb\n\nc'),
            ('plain\nline', 'plain\nline'),
        ],
    )
    def test_created_row_stores_line_feeds_only(self, stage_case, stage, written, stored):
        """Every carriage return line break of a new row is stored as one line feed."""
        row = stage_case.output_model.objects.create(
            **{stage_case.stage_field: stage}, content=written
        )

        row.refresh_from_db()
        assert row.content == stored

    def test_saved_row_stores_line_feeds_only(self, stage_case, stage):
        """A row edited and saved again is normalised as well."""
        row = stage_case.output_model.objects.create(
            **{stage_case.stage_field: stage}, content='kept'
        )
        row.content = 'edited\r\nrow'
        row.save()

        row.refresh_from_db()
        assert row.content == 'edited\nrow'

    @pytest.mark.parametrize('cleanup', [False, True], ids=['allocation', 'cleanup'])
    def test_container_writer_stores_line_feeds_only(self, request, cleanup):
        """A carriage return inside a container output line is stored as a line feed."""
        stage = request.getfixturevalue(
            'cleanup_stage_user' if cleanup else 'allocation_stage_user'
        )
        container = DockerContainer(
            'url', 'rev', stage, 'ssh', 'inventory', 'containers', 'credentials', cleanup=cleanup
        )

        container._store_line('task 1%\rtask 2%\r')  # pylint: disable=protected-access

        assert list(stage.outputs.values_list('content', flat=True)) == ['task 1%\ntask 2%']
