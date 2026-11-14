"""Tests for the sandbox definition list and detail endpoints."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from crczp.sandbox_definition_app.models import Definition
from crczp.sandbox_instance_app.models import Pool

pytestmark = pytest.mark.django_db


def make_pool(definition, created_by, uuid):
    """Create a pool using the given definition."""
    return Pool.objects.create(
        definition=definition,
        max_size=1,
        private_management_key='-----RSA PRIVATE KEY-----',
        public_management_key='ssh-rsa',
        uuid=uuid,
        created_by=created_by,
    )


@pytest.fixture
def definition(created_by):
    """A definition no pool uses."""
    return Definition.objects.create(name='def', url='url', rev='rev', created_by=created_by)


class TestPoolCount:
    """The definition answers carry the number of pools using the definition."""

    def test_detail_counts_zero_one_and_several_pools(self, definition, created_by):
        url = reverse('definition-detail', kwargs={'definition_id': definition.id})
        counts = []
        for index in range(3):
            counts.append(APIClient().get(url).data['pool_count'])
            make_pool(definition, created_by, f'uuid-{index}')

        assert counts == [0, 1, 2]
        assert APIClient().get(url).data['pool_count'] == 3

    def test_list_counts_each_definition_separately(self, definition, created_by):
        other = Definition.objects.create(name='o', url='u2', rev='r', created_by=created_by)
        idle = Definition.objects.create(name='idle', url='u3', rev='r', created_by=created_by)
        make_pool(other, created_by, 'a')
        make_pool(other, created_by, 'b')
        make_pool(definition, created_by, 'c')

        results = APIClient().get(reverse('definition-list')).data['results']

        counts = {item['id']: item['pool_count'] for item in results}
        assert [counts[definition.id], counts[other.id], counts[idle.id]] == [1, 2, 0]

    def test_list_reads_the_pool_table_in_a_constant_number_of_queries(
        self, definition, created_by
    ):
        for index in range(5):
            make_pool(definition, created_by, f'uuid-{index}')
            Definition.objects.create(name='d', url=f'u{index}', rev='r', created_by=created_by)

        with CaptureQueriesContext(connection) as captured:
            APIClient().get(reverse('definition-list'))

        pool_queries = [q for q in captured if Pool._meta.db_table in q['sql']]
        assert len(pool_queries) == 2  # the page count and the page itself


class TestDeleteDefinition:
    """Deleting a definition a pool still uses is refused."""

    def test_in_use_definition_answers_409_naming_the_pools(self, definition, created_by):
        make_pool(definition, created_by, 'uuid')
        make_pool(definition, created_by, 'uuid-2')

        response = APIClient().delete(
            reverse('definition-detail', kwargs={'definition_id': definition.id})
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data == {
            'detail': f'Cannot delete definition (ID="{definition.id}"): '
            '2 pool(s) still use it. Delete those pools first.'
        }
        assert Definition.objects.filter(pk=definition.pk).exists()

    def test_unused_definition_is_deleted(self, definition):
        response = APIClient().delete(
            reverse('definition-detail', kwargs={'definition_id': definition.id})
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Definition.objects.filter(pk=definition.pk).exists()
