"""Tests for the OpenAPI component names of the enums and operations."""

import json
import os
import re
import subprocess  # noqa: S404
import sys
from pathlib import Path

import pytest

from crczp.sandbox_instance_app.lib.allocation_cancellation import CancellationAction

API_PREFIX = '/sandbox-service/api/v1'
SERVICE_ROOT = Path(__file__).resolve().parents[3]

SCHEMA_SETTINGS = """
from crczp.sandbox_service_project.tests.settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'drf_spectacular']  # noqa: F405
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}
"""

PINNED_ENUM_VALUES = {
    'AllocationUnitByCreatorStateEnum': ['ACTIVE'],
    'UnitCancellationActionEnum': [action.value for action in CancellationAction],
}


@pytest.fixture(name='document', scope='module')
def fixture_document(tmp_path_factory):
    """Generate the schema in a separate process set to the drf-spectacular schema class.

    Returns the generated OpenAPI document.
    """
    settings_directory = tmp_path_factory.mktemp('schema_settings')
    (settings_directory / 'schema_settings.py').write_text(SCHEMA_SETTINGS, encoding='utf-8')
    environment = {
        **os.environ,
        'DJANGO_SETTINGS_MODULE': 'schema_settings',
        'PYTHONPATH': os.pathsep.join([
            str(settings_directory),
            str(SERVICE_ROOT),
            *filter(None, os.environ.get('PYTHONPATH', '').split(os.pathsep)),
        ]),
    }
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            'manage.py',
            'spectacular',
            '--format',
            'openapi-json',
            '--validate',
            '--fail-on-warn',
        ],
        cwd=SERVICE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.fixture(name='components')
def fixture_components(document):
    """Return the component schemas of the generated document."""
    return document['components']['schemas']


@pytest.mark.parametrize(('component_name', 'values'), PINNED_ENUM_VALUES.items())
def test_enum_component_is_named_and_lists_its_values(components, component_name, values):
    assert components[component_name]['enum'] == list(values)


def test_no_enum_component_carries_a_hash_suffix(components):
    hashed = [name for name in components if re.fullmatch(r'\w+[0-9a-f]{3}Enum', name)]

    assert hashed == []


def test_topology_operations_have_distinct_explicit_ids(document):
    operation_ids = {
        path.removeprefix(API_PREFIX): operations['get']['operationId']
        for path, operations in document['paths'].items()
        if path.startswith(f'{API_PREFIX}/sandboxes/{{sandbox_uuid}}/topology')
    }
    topology = operation_ids['/sandboxes/{sandbox_uuid}/topology']
    node = operation_ids['/sandboxes/{sandbox_uuid}/topology/{node_name}']

    assert (topology, node) == (
        'sandboxes_topology_retrieve',
        'sandboxes_topology_node_connection_data_retrieve',
    )


def test_error_serializer_component_is_named_after_its_class(components):
    assert components['Error']['required'] == ['detail']
    assert [name for name in components if name.startswith('ErrorSer')] == []


def test_by_creator_state_parameter_refers_to_its_enum(document):
    operation = document['paths'][f'{API_PREFIX}/sandbox-allocation-units/by-creator']['get']
    state = next(
        parameter for parameter in operation['parameters'] if parameter['name'] == 'state'
    )['schema']
    references = [state.get('$ref'), *(part.get('$ref') for part in state.get('allOf', []))]

    assert '#/components/schemas/AllocationUnitByCreatorStateEnum' in references


def test_cancel_allocations_answers_its_unit_results(document, components):
    operation = document['paths'][f'{API_PREFIX}/pools/{{pool_id}}/cancel-allocations']['post']
    response = operation['responses']['201']['content']['application/json']['schema']

    assert response['$ref'] == '#/components/schemas/PoolCancelAllocationsResult'
    assert set(components['UnitCancellation']['properties']) == {
        'unit_id',
        'stack_name',
        'action',
        'stop_pending',
        'reason',
    }


def test_pool_roles_body_is_role_names_keyed_by_user_id(document, components):
    operation = document['paths'][f'{API_PREFIX}/pools/{{pool_id}}/roles']['post']
    request = operation['requestBody']['content']['application/json']['schema']

    assert request['$ref'] == '#/components/schemas/PoolRoleGrants'
    assert components['PoolRoleGrants']['additionalProperties'] == {
        'type': 'array',
        'items': {'type': 'string'},
    }


def test_vpn_config_carries_the_connect_command(components):
    assert 'command' in components['SandboxVpnConfig']['properties']
    assert 'command' not in components['PoolRoleGrants'].get('properties', {})


def test_no_repair_or_maintenance_endpoint_is_documented(document):
    removed = ('/repair', '/reconcile', '/maintenance-runs')

    assert [path for path in document['paths'] if any(part in path for part in removed)] == []
