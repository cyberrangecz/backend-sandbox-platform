"""Tests for the deterministic sorting of the paginated list views."""

from urllib.parse import urlencode

import pytest
from django.core.cache import cache
from drf_spectacular.generators import SchemaGenerator
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from crczp.cloud_commons import Image
from crczp.sandbox_cloud_app.views import ProjectImagesView
from crczp.sandbox_common_lib.common_cloud import list_images
from crczp.sandbox_common_lib.pagination import PageNumberWithPageSizePagination
from crczp.sandbox_definition_app.models import Definition
from crczp.sandbox_definition_app.views import DefinitionListCreateView

pytestmark = pytest.mark.django_db

DEFINITION_COUNT = 5
PAGE_SIZE = 2


@pytest.fixture
def definitions():
    """Create definitions that all share one name."""
    return [
        Definition.objects.create(name='same-name', url=f'https://example.test/{index}.git')
        for index in range(DEFINITION_COUNT)
    ]


def request_page(view, parameters):
    """Send a GET request with the given query parameters to a list view."""
    return view.as_view()(APIRequestFactory().get('/list/', parameters))


def collect_ids(parameters):
    """Return the definition ids of every page, in the order the pages deliver them."""
    ids = []
    page = 1
    while True:
        response = request_page(
            DefinitionListCreateView, {**parameters, 'page': page, 'page_size': PAGE_SIZE}
        )
        ids += [item['id'] for item in response.data['results']]
        if page >= response.data['page_count']:
            return ids
        page += 1


class TestQuerySetSorting:
    """Sorting of a list view backed by a queryset."""

    @staticmethod
    def tied_ids(paged_ids, definitions):
        """Return the ids of the tied definitions in the order the pages delivered them."""
        created_ids = {definition.id for definition in definitions}
        return [identifier for identifier in paged_ids if identifier in created_ids]

    @pytest.mark.parametrize(
        ('parameters', 'tied_order_reversed'),
        [
            ({}, False),
            ({'sort_by': 'name'}, False),
            ({'sort_by': 'name', 'order': 'desc'}, True),
        ],
    )
    def test_equal_values_keep_id_order_without_repeats_or_gaps(
        self, definitions, parameters, tied_order_reversed
    ):
        """Rows equal in the sorted attribute appear once over all pages, ordered by id."""
        paged_ids = collect_ids(parameters)
        expected_ids = [d.id for d in definitions]

        assert len(paged_ids) == len(set(paged_ids))
        assert set(expected_ids) <= set(paged_ids)
        assert self.tied_ids(paged_ids, definitions) == (
            expected_ids[::-1] if tied_order_reversed else expected_ids
        )

    @pytest.mark.parametrize('sort_by', ['?', 'unknown_field', 'created_by__username'])
    def test_sort_by_outside_sortable_fields_is_rejected(self, sort_by):
        """A sort attribute the view does not declare answers 400."""
        response = request_page(DefinitionListCreateView, {'sort_by': sort_by})

        assert response.status_code == 400

    @pytest.mark.parametrize('order', ['up', 'ASC', ''])
    def test_order_outside_asc_and_desc_is_rejected(self, order):
        """An order other than asc or desc answers 400."""
        response = request_page(DefinitionListCreateView, {'order': order})

        assert response.status_code == 400


class TestPostSorting:
    """Results of a POST request keep their order and ignore the sort parameters."""

    @pytest.mark.parametrize('parameters', [{}, {'sort_by': 'name', 'order': 'desc'}, {'sort_by': '?', 'order': 'up'}])
    def test_order_is_kept_and_sort_parameters_are_not_rejected(self, parameters):
        """Items come back in the order they went in, whatever the sort parameters say."""
        request = Request(APIRequestFactory().post('/list/', data={}, QUERY_STRING=urlencode(parameters)))
        items = [{'id': 3}, {'id': 1}, {'id': 2}]

        page = PageNumberWithPageSizePagination().paginate_queryset(
            items, request, DefinitionListCreateView()
        )

        assert page == items


IMAGE_SORT_VALUES = [
    ('name', 'alpha', 'beta'),
    ('os_distro', 'debian', 'ubuntu'),
    ('os_type', 'linux', 'windows'),
    ('disk_format', 'qcow2', 'raw'),
    ('container_format', 'bare', 'ovf'),
    ('visibility', 'private', 'public'),
    ('size', 10, 20),
    ('status', 'active', 'queued'),
    ('min_ram', 512, 1024),
    ('min_disk', 5, 10),
    ('created_at', '2026-01-01', '2026-02-01'),
    ('updated_at', '2026-01-01', '2026-02-01'),
    ('default_user', 'debian', 'kali'),
]


def make_image(**attributes):
    """Create a cloud image with every attribute None unless given."""
    image = Image(
        os_distro=None,
        os_type=None,
        disk_format=None,
        container_format=None,
        visibility=None,
        size=None,
        status=None,
        min_ram=None,
        min_disk=None,
        created_at=None,
        updated_at=None,
        tags=[],
        default_user=None,
        name=None,
        owner_specified={},
    )
    for attribute, value in attributes.items():
        setattr(image, attribute, value)
    return image


class TestListSorting:
    """Sorting of a list view backed by an in-memory list."""

    @pytest.mark.parametrize(
        ('parameters', 'expected_pairs'),
        [
            (
                {},
                [
                    ('alpha', '2026-01-01'),
                    ('alpha', '2026-03-01'),
                    ('beta', '2026-01-01'),
                    ('beta', '2026-02-01'),
                ],
            ),
            (
                {'order': 'desc'},
                [
                    ('beta', '2026-02-01'),
                    ('beta', '2026-01-01'),
                    ('alpha', '2026-03-01'),
                    ('alpha', '2026-01-01'),
                ],
            ),
        ],
    )
    def test_images_are_ordered_by_name_then_created_at(self, mocker, parameters, expected_pairs):
        """Images sort by name and break ties by their creation time, in either direction."""
        images = [
            make_image(name=name, created_at=created_at)
            for name, created_at in [
                ('beta', '2026-02-01'),
                ('alpha', '2026-03-01'),
                ('beta', '2026-01-01'),
                ('alpha', '2026-01-01'),
            ]
        ]
        mocker.patch('crczp.sandbox_cloud_app.views.list_images', return_value=images)

        response = request_page(ProjectImagesView, parameters)

        assert [
            (item['name'], item['created_at']) for item in response.data['results']
        ] == expected_pairs

    @pytest.mark.parametrize(('field', 'low', 'high'), IMAGE_SORT_VALUES)
    @pytest.mark.parametrize('order', ['asc', 'desc'])
    def test_none_values_sort_last_on_every_sortable_field(self, mocker, field, low, high, order):
        """An image holding None in the sorted attribute comes after all the others."""
        images = [make_image(**{field: value}) for value in (None, high, low)]
        mocker.patch('crczp.sandbox_cloud_app.views.list_images', return_value=images)

        response = request_page(ProjectImagesView, {'sort_by': field, 'order': order})

        expected = [low, high, None] if order == 'asc' else [high, low, None]
        if field == 'size':
            expected = [None if value is None else value / 1024**3 for value in expected]
        assert [item[field] for item in response.data['results']] == expected

    def test_none_tiebreaker_values_do_not_break_other_sorts(self, mocker):
        """Images without a name or creation time are sorted by another attribute."""
        images = [make_image(os_distro='b'), make_image(os_distro='a', name='named')]
        mocker.patch('crczp.sandbox_cloud_app.views.list_images', return_value=images)

        response = request_page(ProjectImagesView, {'sort_by': 'os_distro'})

        assert [item['name'] for item in response.data['results']] == ['named', None]

    def test_unknown_image_sort_attribute_is_rejected(self, mocker):
        """An attribute outside the image's sortable fields answers 400."""
        mocker.patch('crczp.sandbox_cloud_app.views.list_images', return_value=[])

        response = request_page(ProjectImagesView, {'sort_by': 'tags'})

        assert response.status_code == 400


class TestImageFilter:
    """Text filters of the image list."""

    @pytest.mark.parametrize('filter_value', ['kali', 'KALI', 'LiNuX'])
    def test_text_filter_ignores_case(self, mocker, filter_value):
        """A text filter matches the attribute whatever the case on either side."""
        images = [make_image(name='Kali-Linux'), make_image(name='debian')]
        mocker.patch('crczp.sandbox_cloud_app.views.list_images', return_value=images)

        response = request_page(ProjectImagesView, {'name': filter_value})

        assert [item['name'] for item in response.data['results']] == ['Kali-Linux']

    def test_other_text_attribute_ignores_case(self, mocker):
        """The case-insensitive match applies to every text attribute, not only the name."""
        images = [make_image(default_user='Debian'), make_image(default_user='kali')]
        mocker.patch('crczp.sandbox_cloud_app.views.list_images', return_value=images)

        response = request_page(ProjectImagesView, {'default_user': 'DEB'})

        assert [item['default_user'] for item in response.data['results']] == ['Debian']

    def test_non_matching_filter_returns_nothing(self, mocker):
        """An image whose attribute does not contain the filter is left out."""
        mocker.patch(
            'crczp.sandbox_cloud_app.views.list_images',
            return_value=[make_image(name='debian')],
        )

        response = request_page(ProjectImagesView, {'name': 'ubuntu'})

        assert response.data['results'] == []


class TestSchema:
    """The OpenAPI description of the sort parameters."""

    @staticmethod
    def sort_parameters(view):
        """Return the paginator's sort query parameters of the view by name."""
        parameters = PageNumberWithPageSizePagination().get_schema_operation_parameters(view)
        return {parameter['name']: parameter for parameter in parameters}

    @pytest.mark.parametrize(
        ('view', 'default', 'tiebreakers'),
        [(DefinitionListCreateView, 'id', 'id'), (ProjectImagesView, 'name', 'name, created_at')],
    )
    def test_sort_by_lists_the_sortable_fields_default_and_tiebreakers(
        self, view, default, tiebreakers
    ):
        """The sort_by parameter is an enum of the view's sortable fields with its default."""
        sort_by = self.sort_parameters(view)['sort_by']

        assert sort_by['schema']['enum'] == list(view.sortable_fields)
        assert sort_by['schema']['default'] == default
        assert tiebreakers in sort_by['description']

    def test_order_is_an_enum_of_asc_and_desc(self):
        """The order parameter accepts only asc and desc."""
        order = self.sort_parameters(DefinitionListCreateView)['order']

        assert order['schema']['enum'] == ['asc', 'desc']
        assert order['schema']['default'] == 'asc'

    def test_full_schema_describes_sorting_of_a_stage_endpoint(self):
        """The whole API schema generates and a stage endpoint offers the sort parameters."""
        schema = SchemaGenerator().get_schema(request=None, public=True)

        [stage_path] = [
            path
            for path in schema['paths']
            if path.endswith('/allocation-requests/{request_id}/stages/networking-ansible')
        ]
        parameters = {
            parameter['name']: parameter for parameter in schema['paths'][stage_path]['get']['parameters']
        }
        assert parameters['sort_by']['schema']['enum'] == ['id']
        assert parameters['order']['schema']['enum'] == ['asc', 'desc']


class TestListImagesOrder:
    """Order of the cloud images served to internal callers."""

    @pytest.fixture(autouse=True)
    def clear_image_cache(self):
        """Drop the image list that list_images caches, before and after the test."""
        cache.clear()
        yield
        cache.clear()

    def test_images_are_ordered_by_name_then_created_at_with_none_last(self, mocker):
        """Images with the same name keep a fixed order and images lacking a value come last."""
        images = [
            make_image(name=None, created_at='2026-01-01'),
            make_image(name='beta', created_at=None),
            make_image(name='beta', created_at='2026-02-01'),
            make_image(name='alpha', created_at='2026-03-01'),
            make_image(name='beta', created_at='2026-01-01'),
        ]
        mocker.patch(
            'crczp.sandbox_common_lib.common_cloud.utils.get_terraform_client'
        ).return_value.list_images.return_value = images

        ordered = list_images(cached=False)

        assert [(image.name, image.created_at) for image in ordered] == [
            ('alpha', '2026-03-01'),
            ('beta', '2026-01-01'),
            ('beta', '2026-02-01'),
            ('beta', None),
            (None, '2026-01-01'),
        ]
