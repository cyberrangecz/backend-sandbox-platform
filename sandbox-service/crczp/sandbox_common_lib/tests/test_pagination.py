"""Unit tests for the sort handling of PageNumberWithPageSizePagination."""

from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import models as auth_models
from django.db.models import QuerySet
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_common_lib.pagination import PageNumberWithPageSizePagination

pytestmark = pytest.mark.django_db


@pytest.fixture(name='users')
def fixture_users() -> QuerySet[auth_models.User]:
    """Three users whose ids and usernames sort in opposite orders.

    Other tests may have loaded users too, so the queryset holds only these three.
    """
    created = [auth_models.User.objects.create(username=name) for name in ('carol', 'bob', 'alice')]
    return auth_models.User.objects.filter(pk__in=[user.pk for user in created])


def paginate(query: dict[str, str], queryset: Any, view: Any = None) -> list[Any]:
    """Run the paginator for a GET with the given query parameters."""
    request = Request(APIRequestFactory().get('/', query))
    page = PageNumberWithPageSizePagination().paginate_queryset(queryset, request, view)
    assert page is not None
    return page


def usernames(page: list[auth_models.User]) -> list[str]:
    """Return the usernames of a page in order."""
    return [user.username for user in page]


@pytest.mark.parametrize(
    ('query', 'expected'),
    [
        ({}, ['carol', 'bob', 'alice']),
        ({'order': 'desc'}, ['alice', 'bob', 'carol']),
        ({'sort_by': 'username'}, ['alice', 'bob', 'carol']),
        ({'sort_by': 'username', 'order': 'desc'}, ['carol', 'bob', 'alice']),
        ({'sort_by': '-username'}, ['carol', 'bob', 'alice']),
        ({'sort_by': '-username', 'order': 'desc'}, ['carol', 'bob', 'alice']),
    ],
)
def test_queryset_sort_direction(users, query, expected):
    """A leading '-' and order=desc both sort descending, together or alone."""
    assert usernames(paginate(query, users)) == expected


def test_queryset_sort_field_mapping(users):
    """A view's sort_field_mapping translates the sort name, keeping its direction."""
    view = SimpleNamespace(sort_field_mapping={'user_id': 'id'})

    page = paginate({'sort_by': '-user_id'}, users, view)

    assert [user.id for user in page] == sorted((user.id for user in users), reverse=True)


def test_queryset_unmapped_field_keeps_descending_order(users):
    """A '-' on a field the mapping does not cover is kept, not dropped."""
    view = SimpleNamespace(sort_field_mapping={'user_id': 'id'})

    page = paginate({'sort_by': '-username'}, users, view)

    assert usernames(page) == ['carol', 'bob', 'alice']


@pytest.mark.parametrize('sort_by', ['no_such_field', '--username', 'allocation_unit_id'])
def test_queryset_unknown_sort_field_is_validation_error(users, sort_by):
    """An unknown sort field is a client error, not an unhandled FieldError."""
    with pytest.raises(exceptions.ValidationError, match='Cannot sort by'):
        paginate({'sort_by': sort_by}, users)


def test_list_sort_honours_leading_minus():
    """Already-serialized data sorts on the field without its '-' prefix."""
    data = [{'name': 'bob'}, {'name': 'carol'}, {'name': 'alice'}]

    page = paginate({'sort_by': '-name'}, data)

    assert [item['name'] for item in page] == ['carol', 'bob', 'alice']
