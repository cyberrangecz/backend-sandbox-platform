"""Custom pagination classes for the sandbox service API."""

from collections import OrderedDict
from typing import Any, override

from django.db.models.query import QuerySet
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response

SORT_ORDERS = ('asc', 'desc')


class SortableViewMixin:
    """
    Declares how a paged list view may be sorted by PageNumberWithPageSizePagination.

    - sortable_fields (required): the attributes a request may sort by.
    - sort_by_default_param: the attribute sorted by when the request names none.
    - sort_tiebreaker_fields: the attributes appended to every sort, in order, so that
      rows equal in the requested attribute keep a stable position across pages.
    """

    sortable_fields: tuple[str, ...]
    sort_by_default_param = 'id'
    sort_tiebreaker_fields: tuple[str, ...] = ('id',)


class PageNumberWithPageSizePagination(PageNumberPagination):
    """
    Custom pagination class with page size specification and deterministic sorting.

    GET results are sorted according to the view's SortableViewMixin attributes; in a list,
    rows holding None in a sorted attribute come last. Results of other methods keep the
    order they arrive in.
    """

    page_size_query_param = 'page_size'
    order_default_param = 'asc'

    @override
    def get_schema_operation_parameters(self, view: Any) -> list[dict[str, Any]]:
        """Extend schema operation parameters with sort_by and order parameters."""
        parameters = super().get_schema_operation_parameters(view)
        tiebreaker_fields = ', '.join(view.sort_tiebreaker_fields)
        parameters += [
            {
                'name': 'sort_by',
                'required': False,
                'in': 'query',
                'description': (
                    'Attribute used to sort result set. '
                    f'The attributes {tiebreaker_fields} '
                    'are always appended last, in the same direction, '
                    'so that pages never overlap.'
                ),
                'schema': {
                    'type': 'string',
                    'enum': list(view.sortable_fields),
                    'default': view.sort_by_default_param,
                },
            },
            {
                'name': 'order',
                'required': False,
                'in': 'query',
                'description': 'Sort order, applied to the tiebreaker attributes as well',
                'schema': {
                    'type': 'string',
                    'enum': list(SORT_ORDERS),
                    'default': self.order_default_param,
                },
            },
        ]
        return parameters

    @override
    def get_paginated_response(self, data: Any) -> Response:
        """Override base class method and extend it with extra args."""
        assert self.page is not None
        assert self.request is not None
        return Response(
            OrderedDict([
                ('page', self.page.number),
                ('page_size', super().get_page_size(self.request)),
                ('page_count', self.page.paginator.num_pages),
                ('count', len(self.page)),
                ('total_count', self.page.paginator.count),
                ('results', data),
            ])
        )

    @override
    def paginate_queryset(
        self, queryset: Any, request: Request, view: Any = None
    ) -> list[Any] | None:
        if request.method == 'GET':
            queryset = self._sorted(queryset, request, view)
        return super().paginate_queryset(queryset, request, view)

    def _sorted(self, queryset: Any, request: Request, view: Any) -> Any:
        """
        Sort the queryset or list of dicts by the requested attribute and the view's
        tiebreaker attributes. In a list, rows holding None in a sorted attribute come last.

        :raise ValidationError: When sort_by is not a sortable attribute or order is invalid.
        """
        sort_by_param = request.GET.get('sort_by', view.sort_by_default_param)
        order_param = request.GET.get('order', self.order_default_param)
        if sort_by_param not in view.sortable_fields:
            raise ValidationError(
                f'Cannot sort by {sort_by_param}. Sortable attributes: '
                f'{", ".join(view.sortable_fields)}.'
            )
        if order_param not in SORT_ORDERS:
            raise ValidationError(f'Invalid order {order_param}. Allowed: asc, desc.')

        sort_keys = [sort_by_param] + [
            key for key in view.sort_tiebreaker_fields if key != sort_by_param
        ]
        descending = order_param == 'desc'

        if isinstance(queryset, QuerySet):
            prefix = '-' if descending else ''
            return queryset.order_by(*(prefix + key for key in sort_keys))
        return sorted(
            queryset,
            key=lambda item: tuple(none_last_key(item.get(key), descending) for key in sort_keys),
            reverse=descending,
        )


def none_last_key(value: Any, descending: bool = False) -> tuple[bool, Any]:
    """
    Return a sort key placing None after every value, whether the sort is applied
    ascending or, with reverse=True, descending.
    """
    return (value is None) != descending, value
