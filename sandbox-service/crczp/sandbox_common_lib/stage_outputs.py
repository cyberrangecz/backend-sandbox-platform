"""
Views serving the output rows a stage of a sandbox request stores while it runs.
"""

import re
from typing import Any, ClassVar

from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from crczp.sandbox_common_lib import utils

LEADING_BLANK_LINES = re.compile(r'\A(?:[ \t\r]*\n)+')
"""Lines holding only whitespace at the start of a text, with their line breaks."""


class StageOutputSerializer(serializers.Serializer[dict[str, Any]]):
    """Output rows of one stage stored after a given row, joined into one text."""

    content = serializers.CharField(
        help_text='Rows stored after from_row in row order, one line each, joined by line '
        'breaks; the read from row 0 starts at its first non-blank row'
    )
    rows = serializers.IntegerField(
        help_text='Id of the last row the content holds, or from_row when it holds none; '
        'the from_row of the next incremental read'
    )


class StageOutputView(APIView):
    """
    get: Retrieve the output rows a stage of a sandbox request stored after from_row.
    A request whose stage is not created yet returns empty content.
    """

    queryset: ClassVar[QuerySet[Any]]
    stage_attribute: ClassVar[str]
    outputs_attribute: ClassVar[str]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='from_row',
                type=int,
                location=OpenApiParameter.QUERY,
                description='Row id (DB relative) after which the returned rows start, '
                'used for incremental fetch',
                required=False,
            )
        ],
        responses={
            200: StageOutputSerializer,
            401: OpenApiResponse(description='Unauthorized'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Not Found'),
            500: OpenApiResponse(description='Internal Server Error'),
        },
    )
    def get(self, request: Request, request_id: int) -> Response:  # pylint: disable=missing-function-docstring
        try:
            from_row = int(request.query_params.get('from_row', 0))
        except (ValueError, TypeError):
            from_row = 0

        sandbox_request = utils.get_object_or_404(self.queryset.model, pk=request_id)
        stage = getattr(sandbox_request, self.stage_attribute, None)
        if stage is None:
            return utils.create_compressed_response({'content': '', 'rows': from_row})

        outputs = list(
            getattr(stage, self.outputs_attribute)
            .filter(id__gt=from_row)
            .order_by('id')
            .values_list('id', 'content')
        )
        content = '\n'.join(output_content for _, output_content in outputs)
        if from_row == 0:
            content = LEADING_BLANK_LINES.sub('', content)
        rows = outputs[-1][0] if outputs else from_row
        return utils.create_compressed_response({'content': content, 'rows': rows})
