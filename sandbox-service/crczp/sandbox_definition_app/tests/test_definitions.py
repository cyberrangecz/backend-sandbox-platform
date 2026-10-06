"""Tests for sandbox definition management."""

import io

import pytest
from django.conf import settings
from django.core.cache import caches
from yamlize import YamlizingError

from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_common_lib.crczp_config import CrczpConfiguration, TopologyCacheMode
from crczp.sandbox_definition_app.lib import definitions
from crczp.sandbox_definition_app.lib.definition_providers import GitlabProvider
from crczp.sandbox_definition_app.models import Definition

pytestmark = pytest.mark.django_db


class TestCreateDefinition:
    """Tests for creating sandbox definitions."""

    URL = 'https://gitlab.example.com/my-repo.git'
    GITHUB_URL = 'https://github.com/org/my-repo.git'
    REV = 'def-rev'
    NAME = 'def-name'

    @pytest.fixture(autouse=True)
    def setup(self, mocker):
        """Set up mocks for DefinitionProvider and topology validation."""
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.DefinitionProvider')
        mocker.patch('crczp.terraform_driver.CrczpTerraformClient.validate_topology_definition')

    @pytest.fixture
    def get_definition_mock(self, mocker):
        """Patch definitions.get_definition and return the patch mock."""
        definition = mocker.Mock()
        definition.name = self.NAME
        definition.network_forwarding = None
        definition.hosts = []
        return mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.get_definition', return_value=definition
        )

    @pytest.fixture
    def topology_definition(self, get_definition_mock):
        """Return the mocked topology definition with a fixed name."""
        return get_definition_mock.return_value

    def test_create_definition(self, mocker, topology_definition, created_by):  # pylint: disable=unused-argument
        """Test that a definition is created and persisted with correct fields."""
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')
        definitions.create_definition(url=self.URL, rev=self.REV, created_by=created_by)

        # Definition name is tested by get
        database_definition = Definition.objects.get(name=self.NAME)
        assert database_definition.url == self.URL
        assert database_definition.rev == self.REV

    def test_create_definition_nonunique(self, mocker, topology_definition, created_by):  # pylint: disable=unused-argument
        """Test that creating a duplicate definition raises ValidationError."""
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')
        definitions.create_definition(url=self.URL, rev=self.REV, created_by=created_by)
        with pytest.raises(exceptions.ValidationError):
            definitions.create_definition(url=self.URL, rev=self.REV, created_by=created_by)

    def test_create_definition_invalid_hosts_group(self, mocker, topology_definition, created_by):
        """Test that a definition with an invalid hosts group raises ValidationError."""
        hosts_group = mocker.Mock()
        hosts_group.name = 'hidden_hosts'
        topology_definition.groups = [hosts_group]
        with pytest.raises(exceptions.ValidationError):
            definitions.create_definition(url=self.URL, rev=self.REV, created_by=created_by)

    def test_create_definition_forwarding_disabled(
        self, mocker, topology_definition, created_by, forwarding_flag
    ):
        """A definition declaring network_forwarding is rejected when the feature is off."""
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')
        topology_definition.network_forwarding = mocker.Mock()
        forwarding_flag(enabled=False)

        with pytest.raises(exceptions.ValidationError, match='network_forwarding_enabled'):
            definitions.create_definition(url=self.URL, rev=self.REV, created_by=created_by)

        assert not Definition.objects.filter(name=self.NAME).exists()

    def test_create_definition_forwarding_enabled(
        self, mocker, topology_definition, created_by, forwarding_flag
    ):
        """With the feature enabled, a definition declaring network_forwarding is created."""
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')
        topology_definition.network_forwarding = mocker.Mock()
        forwarding_flag(enabled=True)

        definitions.create_definition(url=self.URL, rev=self.REV, created_by=created_by)

        assert Definition.objects.get(name=self.NAME).url == self.URL

    def test_create_definition_fresh_import_forces_refresh(
        self, mocker, get_definition_mock, created_by
    ):
        """Test that FRESH_IMPORT mode imports a GitHub definition with force_refresh=True."""
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')
        mocker.patch.object(
            settings.CRCZP_CONFIG, 'topology_cache_mode', TopologyCacheMode.FRESH_IMPORT
        )
        definitions.create_definition(url=self.GITHUB_URL, rev=self.REV, created_by=created_by)
        get_definition_mock.assert_any_call(
            self.GITHUB_URL, self.REV, settings.CRCZP_CONFIG, force_refresh=True
        )

    def test_create_definition_fresh_import_gitlab_does_not_force_refresh(
        self, mocker, get_definition_mock, created_by
    ):
        """Test that FRESH_IMPORT mode does not force refresh for GitLab (GitHub provider only)."""
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')
        mocker.patch.object(
            settings.CRCZP_CONFIG, 'topology_cache_mode', TopologyCacheMode.FRESH_IMPORT
        )
        definitions.create_definition(url=self.URL, rev=self.REV, created_by=created_by)
        get_definition_mock.assert_any_call(
            self.URL, self.REV, settings.CRCZP_CONFIG, force_refresh=False
        )

    def test_create_definition_aggressive_does_not_force_refresh(
        self, mocker, get_definition_mock, created_by
    ):
        """Test that AGGRESSIVE mode imports with force_refresh=False."""
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')
        mocker.patch.object(
            settings.CRCZP_CONFIG, 'topology_cache_mode', TopologyCacheMode.AGGRESSIVE
        )
        definitions.create_definition(url=self.URL, rev=self.REV, created_by=created_by)
        get_definition_mock.assert_any_call(
            self.URL, self.REV, settings.CRCZP_CONFIG, force_refresh=False
        )


class TestLoadDefinition:
    """Tests for loading a topology definition from a stream."""

    def test_load_definition(self, topology_definition_stream):
        """Test that a valid definition is loaded correctly."""
        topology_definition = definitions.load_definition(topology_definition_stream)

        for host in topology_definition.hosts:
            assert host.base_box.image == 'debian-12-x86_64'
            assert host.flavor == 'standard.small'

        for router in topology_definition.routers:
            assert router.base_box.image == 'debian-12-x86_64'
            assert router.flavor == 'standard.small'

    def test_load_definition_invalid_definition(self, mocker, topology_definition_stream):
        """Test that a YamlizingError during load raises ValidationError."""
        topology_definition = mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.TopologyDefinition'
        )
        topology_definition.load.side_effect = YamlizingError('exception-text')

        with pytest.raises(exceptions.ValidationError):
            definitions.load_definition(topology_definition_stream)


class TestGetDefinition:
    """Tests for fetching a definition from a git provider."""

    CFG = CrczpConfiguration()

    @pytest.fixture(autouse=True)
    def clear_topology_cache(self):
        """Isolate the process-global LocMem topology cache between tests."""
        caches['topology_cache'].clear()
        yield
        caches['topology_cache'].clear()

    def test_get_definition(self, mocker):
        """Test that get_definition fetches the file, loads and returns the definition."""
        topology_provider = mocker.MagicMock()
        topology_provider.get_file.return_value = 'test1'
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.get_def_provider',
            return_value=topology_provider,
        )
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.io.StringIO', return_value='test2'
        )
        mock_load_definition = mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.load_definition', return_value='test3'
        )
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.validate_topology_definition',
            return_value='',
        )
        assert definitions.get_definition('url', 'rev', self.CFG) == 'test3'
        mock_load_definition.assert_called_with(definitions.io.StringIO('test1'))

    def test_get_definition_file_not_found(self, mocker):
        """Test that a GitError is raised when the definition file is not found."""
        topology_provider = mocker.MagicMock()
        topology_provider.get_file.side_effect = exceptions.GitError('file not found error')
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.get_def_provider',
            return_value=topology_provider,
        )
        with pytest.raises(exceptions.GitError):
            definitions.get_definition('url', 'rev', self.CFG)

    def test_get_definition_uses_cache_when_not_forced(self, mocker):
        """Test that a cached topology is returned without fetching when not forced."""
        provider = mocker.MagicMock()
        provider.get_rev_sha.return_value = 'stable-sha'
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.get_def_provider',
            return_value=provider,
        )
        caches['topology_cache'].set('definition-url-rev-sha-stable-sha-topology', 'cached-def')

        assert definitions.get_definition('url', 'rev', self.CFG) == 'cached-def'
        provider.get_file.assert_not_called()

    def test_get_definition_force_refresh_bypasses_cache(self, mocker):
        """Test that force_refresh ignores the cached value, fetches fresh, and refreshes it."""
        provider = mocker.MagicMock()
        provider.get_rev_sha.return_value = 'stable-sha'
        provider.get_file.return_value = 'fresh-file'
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.get_def_provider',
            return_value=provider,
        )
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.load_definition',
            return_value='fresh-def',
        )
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')
        cache = caches['topology_cache']
        cache_key = 'definition-url-rev-sha-stable-sha-topology'
        cache.set(cache_key, 'stale-def')

        result = definitions.get_definition('url', 'rev', self.CFG, force_refresh=True)

        assert result == 'fresh-def'
        provider.get_file.assert_called_once()
        assert cache.get(cache_key) == 'fresh-def'


class TestGetDefProvider:  # pylint: disable=too-few-public-methods
    """Tests for the get_def_provider factory function."""

    def test_get_def_provider_gitlab(self):
        """Test that a Gitlab URL returns a GitlabProvider instance."""
        url_git = 'https://gitlab.com/crczp/backend-python/sandbox-service.git'
        cfg_git = CrczpConfiguration()
        assert isinstance(definitions.get_def_provider(url_git, cfg_git), GitlabProvider)


class TestTopologyDefinitionValidation:
    """Tests for topology definition validation."""

    @pytest.mark.skip(reason='fix for this implemented in issue 291')
    def test_incorrect_image_name(self, get_terraform_client, correct_topology):  # pylint: disable=unused-argument
        """Test that an invalid image name raises ValidationError."""
        print(correct_topology)
        bad_topology = correct_topology.replace('image: debian', 'image: debn', 1)
        stream = io.StringIO(bad_topology)
        definition = definitions.load_definition(stream)

        with pytest.raises(exceptions.ValidationError):
            definitions.validate_topology_definition(definition)

    @pytest.mark.parametrize(
        'name, raises',
        [
            ('First-is-not-lower-case12', True),
            ('first-is-lower-case12', False),
            ('invalid_character', True),
            ('-cannot-be-first', True),
            ('cAPITAL-LETTERS-ok', False),
            ('okay-name-without-numbers', False),
            ('2-number-cannot-be-first', True),
        ],
    )
    def test_definition_name_validness(self, get_terraform_client, name, raises, correct_topology):  # pylint: disable=unused-argument
        """Test that topology definition names are validated correctly."""
        new_topology = correct_topology.replace('sandbox-definition', name, 1)
        stream = io.StringIO(new_topology)

        if raises:
            with pytest.raises(exceptions.ValidationError):
                definitions.load_definition(stream)
        else:
            definition = definitions.load_definition(stream)
            definitions.validate_topology_definition(definition)

    def test_unique_host_network_router_names(self, get_terraform_client, correct_topology):  # pylint: disable=unused-argument
        """Test that duplicate names across hosts, networks and routers raise ValidationError."""
        new_topology = (
            correct_topology
            .replace('- name: deb', '- name: same-name', 1)
            .replace('- name: router', '- name: same-name', 1)
            .replace('- name: switch', '- name: same-name', 1)
        )
        stream = io.StringIO(new_topology)
        with pytest.raises(exceptions.ValidationError):
            definitions.load_definition(stream)

    def test_unique_host_network_names(self, get_terraform_client, correct_topology):  # pylint: disable=unused-argument
        """Test that duplicate names between hosts and networks raise ValidationError."""
        new_topology = correct_topology.replace('- name: deb', '- name: same-name', 1).replace(
            '- name: router', '- name: same-name', 1
        )
        stream = io.StringIO(new_topology)
        with pytest.raises(exceptions.ValidationError):
            definitions.load_definition(stream)

    @pytest.mark.parametrize('group_name', ['management', 'routers', 'hosts'])
    def test_redefinition_of_default_groups_fails(
        self,
        get_terraform_client,
        group_name,
        correct_topology,  # pylint: disable=unused-argument
    ):
        """Test that redefining reserved group names raises ValidationError."""
        new_topology = correct_topology.replace(
            '- name: linux-machines', '- name: ' + group_name, 1
        )
        stream = io.StringIO(new_topology)
        definition = definitions.load_definition(stream)

        with pytest.raises(exceptions.ValidationError):
            definitions.validate_topology_definition(definition)

    @pytest.mark.parametrize(
        'group_name', ['ssh_nodes', 'winrm_nodes', 'user_accessible_nodes', 'hidden_hosts']
    )
    def test_redefinition_of_default_groups_with_invalid_names_fails(self, mocker, group_name):
        """Some of the default names use underscore, which wouldn't pass
        the load_definition function.
        To test if the redefinition fails, we need to bypass the load_definition function."""

        hosts_group = mocker.Mock()
        hosts_group.name = group_name

        topology_definition = mocker.Mock()
        topology_definition.name = 'name'
        topology_definition.groups = [hosts_group]
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.get_definition',
            return_value=topology_definition,
        )

        with pytest.raises(exceptions.ValidationError):
            definitions.validate_topology_definition(topology_definition)


BASE = 'debian-12-x86_64'


class VolumeFixtures:
    """Fixtures for the volume image and size checks."""

    AWS = False
    SNAPSHOT = 'snap-0123456789abcdef0'

    @pytest.fixture
    def volumes_topology(self, correct_topology):
        """Build topology.yml text whose host has the given volumes (a list of dicts)."""

        def build(volumes):
            entries = ''.join(
                f'  - {{{", ".join(f"{k}: {v}" for k, v in volume.items())}}}\n'
                for volume in volumes
            )
            marker = '  flavor: standard.small\nrouters:'
            assert marker in correct_topology
            return correct_topology.replace(
                marker, f'  flavor: standard.small\n  volumes:\n{entries}routers:', 1
            )

        return build

    @pytest.fixture
    def make_definition(self, volumes_topology):
        """Build a definition whose host has the given volumes (a list of dicts)."""

        def build(volumes):
            return definitions.load_definition(io.StringIO(volumes_topology(volumes)))

        return build

    @pytest.fixture
    def images(self, mocker):
        """Patch list_images with images of the given name -> min_disk mapping."""

        def patch(min_disks):
            listed = []
            for name, min_disk in min_disks.items():
                listed.append(mocker.Mock(min_disk=min_disk))
                listed[-1].name = name
            mocker.patch(
                'crczp.sandbox_definition_app.lib.definitions.list_images', return_value=listed
            )

        return patch

    @pytest.fixture(autouse=True)
    def terraform_client(self, get_terraform_client):
        """Provide flavors and an empty snapshot lookup for the validation."""
        get_terraform_client.get_snapshot_sizes.return_value = {}
        return get_terraform_client

    @pytest.fixture(autouse=True)
    def provider(self, mocker):
        """Select the cloud provider of the test class."""
        mocker.patch.object(settings, 'AWS_PROVIDER_CONFIGURED', self.AWS)


class TestVolumeValidation(VolumeFixtures):
    """Tests for volume images and sizes on OpenStack, checked only where builds start."""

    URL = 'https://gitlab.example.com/my-repo.git'

    @pytest.fixture
    def git_definition(self, mocker, volumes_topology):
        """Serve topology.yml with the given volumes from a mocked git provider."""
        caches['topology_cache'].clear()
        provider = mocker.MagicMock()
        provider.get_rev_sha.return_value = 'sha'
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.get_def_provider', return_value=provider
        )

        def serve(volumes):
            provider.get_file.return_value = volumes_topology(volumes)

        yield serve
        caches['topology_cache'].clear()

    def test_openstack_missing_volume_image_rejected(self, make_definition, images):
        """A volume image absent from the image list is rejected on OpenStack."""
        images({BASE: None})
        definition = make_definition([{'size': 10}, {'size': 5, 'image': 'data-image'}])

        with pytest.raises(exceptions.ValidationError, match='Image data-image was not found'):
            definitions.validate_volumes(definition)

    def test_openstack_existing_volume_image_accepted(self, make_definition, images):
        """A volume image present in the image list passes on OpenStack."""
        images({BASE: None, 'data-image': None})
        definition = make_definition([{'size': 10}, {'size': 5, 'image': 'data-image'}])

        definitions.validate_volumes(definition)

    def test_system_volume_below_base_image_minimum_rejected(self, make_definition, images):
        """volumes[0] smaller than the base image min_disk is rejected."""
        images({BASE: 20})
        definition = make_definition([{'size': 10}])

        with pytest.raises(
            exceptions.ValidationError,
            match='Host deb: volume 0 size 10 GB is below the 20 GB minimum',
        ):
            definitions.validate_volumes(definition)

    def test_system_volume_equal_to_base_image_minimum_accepted(self, make_definition, images):
        """volumes[0] equal to the base image min_disk passes."""
        images({BASE: 20})

        definitions.validate_volumes(make_definition([{'size': 20}]))

    def test_extra_volume_below_image_minimum_rejected(self, make_definition, images):
        """An image-backed extra volume smaller than its image min_disk is rejected on OpenStack."""
        images({BASE: None, 'data-image': 8})
        definition = make_definition([{'size': 10}, {'size': 5, 'image': 'data-image'}])

        with pytest.raises(
            exceptions.ValidationError,
            match='Host deb: volume 1 size 5 GB is below the 8 GB minimum of image data-image',
        ):
            definitions.validate_volumes(definition)

    @pytest.mark.parametrize('min_disk', [None, 0])
    def test_unset_minimum_skips_size_check(self, make_definition, images, min_disk):
        """An unset or zero min_disk disables the size check."""
        images({BASE: min_disk, 'data-image': min_disk})
        definition = make_definition([{'size': 1}, {'size': 1, 'image': 'data-image'}])

        definitions.validate_volumes(definition)

    def test_topology_validation_ignores_volumes(self, make_definition, images):
        """Reading a definition does not depend on the volume checks."""
        images({BASE: 20})

        definitions.validate_topology_definition(
            make_definition([{'size': 10}, {'size': 5, 'image': 'data-image'}])
        )

    def test_get_definition_ignores_volumes(self, git_definition, images):
        """An already imported definition stays readable when its volumes fail the checks."""
        images({BASE: 20})
        git_definition([{'size': 10}])

        topology_definition = definitions.get_definition('url', 'rev', settings.CRCZP_CONFIG)

        assert topology_definition.hosts[0].volumes[0].size == 10

    def test_create_definition_checks_volumes(self, git_definition, images, created_by):
        """Importing a definition runs the volume checks."""
        images({BASE: 20})
        git_definition([{'size': 10}])

        with pytest.raises(exceptions.ValidationError, match='volume 0 size 10 GB is below'):
            definitions.create_definition(url=self.URL, rev='rev', created_by=created_by)

        assert not Definition.objects.filter(url=self.URL).exists()


class TestAwsVolumeValidation(VolumeFixtures):
    """Tests for volume snapshots and sizes on AWS."""

    AWS = True

    @pytest.mark.parametrize('snapshot', ['snap-0123abcd', VolumeFixtures.SNAPSHOT])
    def test_aws_snapshot_id_accepted(self, make_definition, images, terraform_client, snapshot):
        """An EBS snapshot id owned by the account is accepted without being listed as an image."""
        images({BASE: None})
        terraform_client.get_snapshot_sizes.return_value = {snapshot: 5}
        definition = make_definition([{'size': 10}, {'size': 5, 'image': snapshot}])

        definitions.validate_volumes(definition)

        terraform_client.get_snapshot_sizes.assert_called_once_with([snapshot])

    @pytest.mark.parametrize(
        'image',
        ['debian-12', 'snap-0123456789ABCDEF0', 'snap-0123abcdx', 'snap-0123456789abcdef'],
    )
    def test_aws_image_name_rejected(self, make_definition, images, terraform_client, image):
        """A volume image that is not an EBS snapshot id is rejected on AWS before any lookup."""
        images({BASE: None, image: None})
        definition = make_definition([{'size': 10}, {'size': 5, 'image': image}])

        with pytest.raises(exceptions.ValidationError, match=f'{image} is not an EBS snapshot id'):
            definitions.validate_volumes(definition)

        terraform_client.get_snapshot_sizes.assert_not_called()

    def test_aws_snapshot_not_owned_rejected(self, make_definition, images, terraform_client):
        """A snapshot the lookup does not return (missing or of another account) is rejected."""
        images({BASE: None})
        terraform_client.get_snapshot_sizes.return_value = {}
        definition = make_definition([{'size': 10}, {'size': 5, 'image': self.SNAPSHOT}])

        with pytest.raises(
            exceptions.ValidationError,
            match=f'{self.SNAPSHOT} is not an EBS snapshot owned by the platform account',
        ):
            definitions.validate_volumes(definition)

    def test_aws_volume_below_snapshot_size_rejected(
        self, make_definition, images, terraform_client
    ):
        """On AWS an extra volume smaller than its snapshot is rejected."""
        images({BASE: None})
        terraform_client.get_snapshot_sizes.return_value = {self.SNAPSHOT: 8}
        definition = make_definition([{'size': 10}, {'size': 5, 'image': self.SNAPSHOT}])

        with pytest.raises(
            exceptions.ValidationError,
            match=f'volume 1 size 5 GB is below the 8 GB size of snapshot {self.SNAPSHOT}',
        ):
            definitions.validate_volumes(definition)

    def test_aws_volume_equal_to_snapshot_size_accepted(
        self, make_definition, images, terraform_client
    ):
        """On AWS an extra volume as large as its snapshot passes, whatever the image min_disk."""
        images({BASE: 8, self.SNAPSHOT: 20})
        terraform_client.get_snapshot_sizes.return_value = {self.SNAPSHOT: 8}

        definitions.validate_volumes(
            make_definition([{'size': 8}, {'size': 8, 'image': self.SNAPSHOT}])
        )


class TestNetworkForwardingEnabled:
    """Tests for the network_forwarding_enabled feature flag."""

    def test_rejected_when_disabled(self, topology_definition_forwarding, forwarding_flag):
        """A definition declaring network_forwarding is rejected when the feature is off."""
        forwarding_flag(enabled=False)

        with pytest.raises(exceptions.ValidationError, match='network_forwarding_enabled'):
            definitions.validate_network_forwarding_enabled(topology_definition_forwarding)

    def test_accepted_when_enabled(self, topology_definition_forwarding, forwarding_flag):
        """A definition declaring network_forwarding passes when the feature is on."""
        forwarding_flag(enabled=True)

        definitions.validate_network_forwarding_enabled(topology_definition_forwarding)

    def test_without_forwarding_accepted_when_disabled(
        self, topology_definition_stream, forwarding_flag
    ):
        """A definition without network_forwarding does not depend on the flag."""
        forwarding_flag(enabled=False)

        definitions.validate_network_forwarding_enabled(
            definitions.load_definition(topology_definition_stream)
        )

    @pytest.mark.usefixtures('get_terraform_client')
    def test_topology_validation_ignores_flag(
        self, topology_definition_forwarding, forwarding_flag
    ):
        """Reading a definition must not depend on the current value of the flag."""
        forwarding_flag(enabled=False)

        definitions.validate_topology_definition(topology_definition_forwarding)

    @pytest.mark.usefixtures('get_terraform_client')
    def test_get_definition_ignores_flag(
        self, mocker, topology_definition_forwarding, forwarding_flag
    ):
        """An already imported definition stays readable after the flag is switched off."""
        forwarding_flag(enabled=False)
        caches['topology_cache'].clear()
        provider = mocker.MagicMock()
        provider.get_rev_sha.return_value = 'sha'
        provider.get_file.return_value = 'topology'
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.get_def_provider', return_value=provider
        )
        mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.load_definition',
            return_value=topology_definition_forwarding,
        )

        assert (
            definitions.get_definition('url', 'rev', settings.CRCZP_CONFIG)
            is topology_definition_forwarding
        )


class TestDockerContainerValidation:
    """Tests for container_mappings in docker container validation."""

    @pytest.fixture
    def map_container(self, mocker):
        """Map a container to a single host with the given managed value."""

        def build(*, managed: bool) -> None:
            host = mocker.Mock(managed=managed)
            host.name = 'appliance'
            mocker.patch(
                'crczp.sandbox_definition_app.lib.definitions.get_definition',
                return_value=mocker.Mock(hosts=[host]),
            )
            container = mocker.Mock(image='nginx', dockerfile=None)
            container.name = 'web'
            containers = mocker.Mock(
                containers=[container],
                container_mappings=[mocker.Mock(container='web', host='appliance')],
            )
            mocker.patch(
                'crczp.sandbox_definition_app.lib.definitions.get_containers',
                return_value=containers,
            )

        return build

    def test_container_on_managed_host_accepted(self, mocker, map_container):
        """A container mapped to a managed host validates."""
        map_container(managed=True)

        definitions.validate_docker_containers('url', 'rev', mocker.Mock())

    def test_container_on_unmanaged_host_rejected(self, mocker, map_container):
        """Stage one never installs docker on an unmanaged host, so mapping to it is rejected."""
        map_container(managed=False)

        with pytest.raises(exceptions.ValidationError, match='"appliance" is managed: false'):
            definitions.validate_docker_containers('url', 'rev', mocker.Mock())
