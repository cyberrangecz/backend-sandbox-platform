"""Custom pagination classes for the sandbox service API."""

from collections import OrderedDict
from typing import Any, override

from django.core.exceptions import FieldError
from django.db.models.query import QuerySet
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response

from crczp.sandbox_common_lib import exceptions


class PageNumberWithPageSizePagination(PageNumberPagination):
    """Custom pagination class with page size specification."""

    page_size_query_param = 'page_size'
    sort_by_default_param = 'id'
    order_default_param = 'asc'
    sorting_default_values: dict[str, Any] = {}

    @override
    def get_schema_operation_parameters(self, view: Any) -> list[dict[str, Any]]:
        """Extend schema operation parameters with sort_by and order parameters."""
        parameters = super().get_schema_operation_parameters(view)
        parameters += [
            {
                'name': 'sort_by',
                'required': False,
                'in': 'query',
                'description': 'Attribute used to sort result set',
                'schema': {'type': 'string'},
            },
            {
                'name': 'order',
                'required': False,
                'in': 'query',
                'description': 'Sort order',
                'schema': {'type': 'string'},
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
        sort_by_param = request.GET.get('sort_by', self.sort_by_default_param)
        order_param = request.GET.get('order', self.order_default_param)
        # A leading '-' asks for descending order on its own, whatever `order` says.
        sort_field = sort_by_param.removeprefix('-')
        descending = order_param == 'desc' or sort_field != sort_by_param

        if isinstance(queryset, QuerySet):
            # A view may accept a sort name that differs from the model field, e.g. the
            # pool allocation-unit list takes the frontend's 'allocation_unit_id' for 'id'.
            sort_field = getattr(view, 'sort_field_mapping', {}).get(sort_field, sort_field)
            try:
                queryset = queryset.order_by(f'-{sort_field}' if descending else sort_field)
            except FieldError:
                raise exceptions.ValidationError(
                    f'Cannot sort by {sort_by_param!r}: unknown field.'
                ) from None
        else:
            queryset = sorted(
                queryset,
                key=lambda item: self._ensure_comparable(item.get(sort_field, ''), sort_field),
                reverse=descending,
            )
        # The base implementation only needs a sized sequence (it hands the value to
        # django.core.paginator.Paginator), which is why this override also accepts the
        # sorted list; only the stubs narrow the parameter to QuerySet.
        return super().paginate_queryset(
            queryset,  # ty: ignore[invalid-argument-type]
            request,
            view,
        )

    def _ensure_comparable(self, value: Any, sort_by: str) -> Any:
        """
        None values in parameters cause problems with sorting. This method
        replaces None with orderable values in the sorting function.

        If you want to add sorting by a parameter that can be None, adjust
        paginator.sorting_default_values in the given list view
        """
        if value is not None:
            return value

        default = self.sorting_default_values.get(sort_by, None)
        if default is not None:
            return default

        raise ValueError(f'Unexpected None value for the attribute {sort_by}, cannot sort.')
