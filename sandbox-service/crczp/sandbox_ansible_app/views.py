"""REST API views for Ansible stage operations."""

from typing import override

import structlog
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Model
from django.http import Http404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics

from crczp.sandbox_ansible_app import serializers
from crczp.sandbox_ansible_app.models import (
    NetworkingAnsibleAllocationStage,
    NetworkingAnsibleCleanupStage,
    UserAnsibleAllocationStage,
    UserAnsibleCleanupStage,
)
from crczp.sandbox_common_lib import stage_outputs
from crczp.sandbox_common_lib.pagination import SortableViewMixin
from crczp.sandbox_instance_app.models import AllocationRequest, CleanupRequest

LOG = structlog.get_logger()


class StageDetailView[StageT: Model](SortableViewMixin, generics.RetrieveAPIView[StageT]):
    """Retrieves the stage a request holds under `stage_attribute`; 404 while it does not exist."""

    stage_attribute: str
    lookup_url_kwarg = 'request_id'
    sortable_fields = ('id',)

    @override
    def get_object(self) -> StageT:
        request = super().get_object()
        try:
            return getattr(request, self.stage_attribute)
        except ObjectDoesNotExist as exc:
            raise Http404('The request has no such stage.') from exc


COMMON_RESPONSE_PATTERNS = {
    401: OpenApiResponse(description='Unauthorized'),
    403: OpenApiResponse(description='Forbidden'),
    404: OpenApiResponse(description='Not Found'),
    500: OpenApiResponse(description='Internal Server Error'),
}


@extend_schema(
    methods=['GET'],
    responses={
        200: serializers.NetworkingAnsibleAllocationStageSerializer(many=True),
        **COMMON_RESPONSE_PATTERNS,
    },
)
class NetworkingAnsibleAllocationStageDetailView(StageDetailView[NetworkingAnsibleAllocationStage]):
    """
    get: Retrieve a `Networking Ansible` Allocation stage.
    """

    serializer_class = serializers.NetworkingAnsibleAllocationStageSerializer
    queryset = AllocationRequest.objects.all()
    stage_attribute = 'networkingansibleallocationstage'


@extend_schema(
    methods=['GET'],
    responses={
        200: serializers.UserAnsibleAllocationStageSerializer(many=True),
        **COMMON_RESPONSE_PATTERNS,
    },
)
class UserAnsibleAllocationStageDetailView(StageDetailView[UserAnsibleAllocationStage]):
    """
    get: Retrieve a `User Ansible` Allocation stage.
    """

    serializer_class = serializers.UserAnsibleAllocationStageSerializer
    queryset = AllocationRequest.objects.all()
    stage_attribute = 'useransibleallocationstage'


@extend_schema(
    methods=['GET'],
    responses={
        200: serializers.NetworkingAnsibleCleanupStageSerializer(many=True),
        **COMMON_RESPONSE_PATTERNS,
    },
)
class NetworkingAnsibleCleanupStageDetailView(StageDetailView[NetworkingAnsibleCleanupStage]):
    """
    get: Retrieve a `Networking Ansible` Cleanup stage.
    """

    serializer_class = serializers.NetworkingAnsibleCleanupStageSerializer
    queryset = CleanupRequest.objects.all()
    stage_attribute = 'networkingansiblecleanupstage'


@extend_schema(
    methods=['GET'],
    responses={
        200: serializers.UserAnsibleCleanupStageSerializer(many=True),
        **COMMON_RESPONSE_PATTERNS,
    },
)
class UserAnsibleCleanupStageDetailView(StageDetailView[UserAnsibleCleanupStage]):
    """
    get: Retrieve a `User Ansible` Cleanup stage.
    """

    serializer_class = serializers.UserAnsibleCleanupStageSerializer
    queryset = CleanupRequest.objects.all()
    stage_attribute = 'useransiblecleanupstage'


class NetworkingAnsibleOutputListView(stage_outputs.StageOutputView):
    """get: Retrieve the output rows of a `Networking Ansible` allocation stage."""

    queryset = AllocationRequest.objects.all()
    stage_attribute = 'networkingansibleallocationstage'
    outputs_attribute = 'outputs'


class UserAnsibleOutputListView(stage_outputs.StageOutputView):
    """get: Retrieve the output rows of a `User Ansible` allocation stage."""

    queryset = AllocationRequest.objects.all()
    stage_attribute = 'useransibleallocationstage'
    outputs_attribute = 'outputs'


class NetworkingAnsibleCleanupOutputListView(stage_outputs.StageOutputView):
    """get: Retrieve the output rows of a `Networking Ansible` cleanup stage."""

    queryset = CleanupRequest.objects.all()
    stage_attribute = 'networkingansiblecleanupstage'
    outputs_attribute = 'outputs'


class UserAnsibleCleanupOutputListView(stage_outputs.StageOutputView):
    """get: Retrieve the output rows of a `User Ansible` cleanup stage."""

    queryset = CleanupRequest.objects.all()
    stage_attribute = 'useransiblecleanupstage'
    outputs_attribute = 'outputs'
