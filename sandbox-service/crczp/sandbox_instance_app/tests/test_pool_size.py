"""Tests for the size field of the pool serializer."""

import pytest

from crczp.sandbox_instance_app.serializers import PoolSerializer

pytestmark = pytest.mark.django_db


def serialized_size(pool) -> int:
    """Return the size the pool serializer gives for the pool."""
    return PoolSerializer().fields['size'].to_representation(
        PoolSerializer().fields['size'].get_attribute(pool)
    )


class TestPoolSize:
    """The pool size is the stored value and is never recalculated while reading."""

    def test_empty_pool_shows_zero(self, pool):
        """A pool without allocation units shows size 0."""
        assert serialized_size(pool) == 0

    def test_pool_shows_the_stored_size(self, pool):
        """The serialized size is the stored value, not a recount of its units."""
        pool.size = 5

        assert serialized_size(pool) == 5

    def test_reading_a_zero_size_pool_does_not_query_the_database(
        self, pool, django_assert_num_queries
    ):
        """Reading a zero-size pool neither reads nor writes the database."""
        pool.size = 0
        with django_assert_num_queries(0):
            serialized_size(pool)
