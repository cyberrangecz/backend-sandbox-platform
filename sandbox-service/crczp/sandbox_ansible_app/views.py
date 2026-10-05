"""REST API views for Ansible stage operations."""

from typing import override

import structlog
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
from crczp.sandbox_instance_app.models import AllocationRequest, CleanupRequest

LOG = structlog.get_logger()

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
class NetworkingAnsibleAllocationStageDetailView(
    generics.RetrieveAPIView[NetworkingAnsibleAllocationStage]
):
    """
    get: Retrieve a `Networking Ansible` Allocation stage.
    """

    serializer_class = serializers.NetworkingAnsibleAllocationStageSerializer
    queryset = AllocationRequest.objects.all()
    lookup_url_kwarg = 'request_id'

    @override
    def get_object(self) -> NetworkingAnsibleAllocationStage:
        request = super().get_object()
        return request.networkingansibleallocationstage


@extend_schema(
    methods=['GET'],
    responses={
        200: serializers.UserAnsibleAllocationStageSerializer(many=True),
        **COMMON_RESPONSE_PATTERNS,
    },
)
class UserAnsibleAllocationStageDetailView(generics.RetrieveAPIView[UserAnsibleAllocationStage]):
    """
    get: Retrieve a `User Ansible` Allocation stage.
    """

    serializer_class = serializers.UserAnsibleAllocationStageSerializer
    queryset = AllocationRequest.objects.all()
    lookup_url_kwarg = 'request_id'

    @override
    def get_object(self) -> UserAnsibleAllocationStage:
        request = super().get_object()
        return request.useransibleallocationstage


@extend_schema(
    methods=['GET'],
    responses={
        200: serializers.NetworkingAnsibleCleanupStageSerializer(many=True),
        **COMMON_RESPONSE_PATTERNS,
    },
)
class NetworkingAnsibleCleanupStageDetailView(
    generics.RetrieveAPIView[NetworkingAnsibleCleanupStage]
):
    """
    get: Retrieve a `Networking Ansible` Cleanup stage.
    """

    serializer_class = serializers.NetworkingAnsibleCleanupStageSerializer
    queryset = CleanupRequest.objects.all()
    lookup_url_kwarg = 'request_id'

    @override
    def get_object(self) -> NetworkingAnsibleCleanupStage:
        request = super().get_object()
        return request.networkingansiblecleanupstage


@extend_schema(
    methods=['GET'],
    responses={
        200: serializers.UserAnsibleCleanupStageSerializer(many=True),
        **COMMON_RESPONSE_PATTERNS,
    },
)
class UserAnsibleCleanupStageDetailView(generics.RetrieveAPIView[UserAnsibleCleanupStage]):
    """
    get: Retrieve a `User Ansible` Cleanup stage.
    """

    serializer_class = serializers.UserAnsibleCleanupStageSerializer
    queryset = CleanupRequest.objects.all()
    lookup_url_kwarg = 'request_id'

    @override
    def get_object(self) -> UserAnsibleCleanupStage:
        request = super().get_object()
        return request.useransiblecleanupstage


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
