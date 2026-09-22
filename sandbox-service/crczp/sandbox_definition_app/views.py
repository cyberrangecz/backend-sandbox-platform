"""REST API views for sandbox definition management."""

from typing import Any, override

import structlog
from django.conf import settings
from django.contrib.auth.models import User
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiRequest,
    OpenApiResponse,
    extend_schema,
)
from generator.var_generator import generate
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from crczp.cloud_commons import UNIVERSAL_ROLES
from crczp.sandbox_common_lib import exceptions, utils
from crczp.sandbox_common_lib.swagger_typing import (
    DefinitionRequestSerializer,
    SandboxDefinitionSerializer,
)
from crczp.sandbox_definition_app import serializers
from crczp.sandbox_definition_app.lib import definitions
from crczp.sandbox_definition_app.lib.definition_providers import DefinitionProvider
from crczp.sandbox_definition_app.models import Definition
from crczp.sandbox_instance_app import serializers as instance_serializers
from crczp.sandbox_instance_app.lib import sandboxes
from crczp.sandbox_instance_app.lib.topology import Topology
from crczp.sandbox_uag.permissions import AdminPermission, DesignerPermission, OrganizerPermission

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
        200: OpenApiResponse(
            description='List of Sandbox Definitions', response=SandboxDefinitionSerializer
        ),
        **{k: v for k, v in utils.ERROR_RESPONSES.items() if k in [401, 403, 500]},
    },
)
class DefinitionListCreateView(generics.ListCreateAPIView[Definition]):
    """
    get: Retrieve a list of sandbox definitions.
    """

    queryset = Definition.objects.all()
    serializer_class = serializers.DefinitionSerializer

    @extend_schema(
        request=OpenApiRequest(DefinitionRequestSerializer),
        responses={
            201: OpenApiResponse(response=SandboxDefinitionSerializer),
            **{k: v for k, v in utils.ERROR_RESPONSES.items() if k in [400, 401, 403, 500]},
        },
    )
    @override
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """
        Create a new sandbox definition. Optional parameter *rev* defaults to master.
        """
        url: str = request.data.get('url') or ''
        rev = request.data.get('rev', 'master')
        created_by = request.user if isinstance(request.user, User) else None
        definition = definitions.create_definition(url, created_by, rev)
        serializer = self.serializer_class(definition)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(
    methods=['GET'], responses={200: SandboxDefinitionSerializer, **COMMON_RESPONSE_PATTERNS}
)
@extend_schema(methods=['DELETE'], responses={**COMMON_RESPONSE_PATTERNS})
class DefinitionDetailDeleteView(generics.RetrieveDestroyAPIView[Definition]):
    """
    get: Retrieve the definition.
    delete: Delete the definition.
    There can't exist a pool associated with this definition or delete fails.
    """

    queryset = Definition.objects.all()
    serializer_class = serializers.DefinitionSerializer
    lookup_url_kwarg = 'definition_id'


@extend_schema(
    methods=['GET'],
    responses={
        200: OpenApiResponse(
            description='List of Definition Refs',
            response=serializers.DefinitionSerializer(many=True),
        ),
        **COMMON_RESPONSE_PATTERNS,
    },
)
class DefinitionRefsListView(generics.ListAPIView[Any]):
    """
    get: Retrieve a list of definition refs (branches and tags).
    """

    serializer_class = serializers.DefinitionRevSerializer

    @override
    def get_queryset(self) -> Any:
        def_id = self.kwargs.get('definition_id')
        definition = utils.get_object_or_404(Definition, pk=def_id)
        provider: DefinitionProvider = definitions.get_def_provider(
            definition.url, settings.CRCZP_CONFIG
        )
        return provider.get_refs()


@extend_schema(
    methods=['GET'],
    responses={
        200: instance_serializers.DefinitionTopologySerializer(),
        **COMMON_RESPONSE_PATTERNS,
    },
)
class DefinitionTopologyView(generics.RetrieveAPIView[Any]):
    """
    get: Retrieve topology visualisation data from TopologyDefinition

    Previews every entity of the definition regardless of role, each carrying the role
    names its own declaration names: there is no pool yet to resolve a requester's roles
    against.
    """

    queryset = Definition.objects.all()
    lookup_url_kwarg = 'definition_id'
    serializer_class = instance_serializers.DefinitionTopologySerializer

    @override
    def get_object(self) -> Any:
        definition = super().get_object()
        topology_definition = definitions.get_definition(
            definition.url, definition.rev, settings.CRCZP_CONFIG
        )
        containers = definitions.get_containers(
            definition.url, definition.rev, settings.CRCZP_CONFIG
        )
        client = utils.get_terraform_client()
        return Topology(
            client.get_topology_instance(topology_definition, containers), UNIVERSAL_ROLES
        )


@extend_schema(
    methods=['POST'],
    responses={201: serializers.LocalVariableSerializer(many=True), **COMMON_RESPONSE_PATTERNS},
)
class LocalSandboxVariablesView(generics.CreateAPIView[Any]):
    """View to generate and post variables for local sandboxes."""

    queryset = Definition.objects.all()
    lookup_url_kwarg = 'definition_id'
    serializer_class = serializers.LocalSandboxVariablesSerializer

    @override
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Generate variables for local sandboxes, send it to answers-storage."""
        user_id = request.data.get('user_id')
        access_token = request.data.get('access_token')

        definition = self.get_object()
        variables = definitions.get_variables(definition.url, definition.rev, settings.CRCZP_CONFIG)
        # FIXME: user_id/access_token are read straight off request.data and are therefore
        # Optional, while generate() expects an int seed and post_answers() an int/str pair.
        # This view declares LocalSandboxVariablesSerializer (user_id=IntegerField,
        # access_token=CharField) as its serializer_class but never validates with it, so a
        # request missing user_id currently seeds the generator with None (non-deterministic
        # output) instead of being rejected with 400. Fixing that changes the response for
        # malformed requests, so it is left to a deliberate API change.
        generate(variables, user_id)  # ty: ignore[invalid-argument-type]
        sandboxes.post_answers(
            user_id,  # ty: ignore[invalid-argument-type]
            access_token,  # ty: ignore[invalid-argument-type]
            variables,
        )

        serialized_variables = serializers.LocalVariableSerializer(variables, many=True)
        return Response(serialized_variables.data)


@extend_schema(
    methods=['GET'],
    responses={200: OpenApiResponse(description='Variables List'), **COMMON_RESPONSE_PATTERNS},
)
class DefinitionVariablesView(APIView):
    """View to retrieve APG variable names from a definition."""

    queryset = Definition.objects.none()

    # noinspection PyMethodMayBeStatic
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # pylint: disable=unused-argument
        """Retrieve APG variables from TopologyDefinition, empty list if variables.yml was not
        found."""
        definition = utils.get_object_or_404(Definition, pk=kwargs.get('definition_id'))
        variable_names = []
        try:
            variables = definitions.get_variables(
                definition.url, definition.rev, settings.CRCZP_CONFIG
            )
            variable_names = [variable.name for variable in variables]
        except exceptions.GitError:
            pass
        return Response({'variables': variable_names})


@extend_schema(
    methods=['GET'],
    parameters=[
        OpenApiParameter(
            'rev',
            str,
            description="Revision to read. Defaults to the definition's stored revision.",
        )
    ],
    responses={
        200: OpenApiResponse(
            response=serializers.DefinitionRolesSerializer,
            description='The declared role names, with the revision that answered',
        ),
        **COMMON_RESPONSE_PATTERNS,
    },
)
class DefinitionRolesView(APIView):
    """View to retrieve the role names a definition declares, at a revision."""

    queryset = Definition.objects.none()
    permission_classes = [DesignerPermission | OrganizerPermission | AdminPermission]

    # noinspection PyMethodMayBeStatic
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Return the definition's declared role names at the requested (or stored) revision."""
        definition = utils.get_object_or_404(Definition, pk=kwargs.get('definition_id'))
        rev = request.query_params.get('rev') or definition.rev
        provider = definitions.get_def_provider(definition.url, settings.CRCZP_CONFIG)
        rev_sha = provider.get_rev_sha(rev)
        topology_definition = definitions.get_definition(
            definition.url, rev_sha, settings.CRCZP_CONFIG
        )
        roles = sorted(topology_definition.get_declared_roles())
        return Response(
            serializers.DefinitionRolesSerializer({
                'rev': rev,
                'rev_sha': rev_sha,
                'roles': roles,
            }).data
        )
