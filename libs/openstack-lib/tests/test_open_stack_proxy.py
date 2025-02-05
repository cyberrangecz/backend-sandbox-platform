import pytest
from ruamel.yaml import YAML
from crczp.cloud_commons import CrczpException, StackException, HardwareUsage, Limits, Quota, QuotaSet, Image
from novaclient.exceptions import ClientException as NovaClientException

from crczp.openstack_driver import utils
from crczp.openstack_driver.open_stack_proxy import OpenStackProxy


class TestOpenStackProxy:
    @pytest.fixture()
    def open_stack_proxy(self, mocker, trc):
        # Mock session and Jinja2 env
        mock_session = mocker.MagicMock()
        auth_url = "url"
        application_credential_id = "app_id"
        application_credential_secret = "app_secret"

        glance_client = utils.get_client("glance", mock_session)
        nova_client = utils.get_client("nova", mock_session)
        neutron_client = utils.get_client("neutron", mock_session)

        open_stack_proxy = OpenStackProxy(
            glance_client, nova_client, neutron_client, auth_url, application_credential_id, application_credential_secret, trc
        )

        return open_stack_proxy

    @pytest.fixture()
    def stack_resources(self):
        resources = {
            "physical_resource_id": "node_id",
            "creation_time": "22",
            "updated_time": "33",
            "attributes": {
                "status": "ACTIVE",
                "flavor": {"original_name": "name"},
                "image": {"id": 2},
                "addresses": {"network_name": [{"addr": "10.10.10.2"}]},
            },
        }

        return resources

    @pytest.fixture()
    def mock_get_quota_set(self, mocker, open_stack_proxy):
        def get_quota_set(quotas):
            quota_set = QuotaSet(**quotas)
            open_stack_proxy.get_quota_set = mocker.MagicMock()
            open_stack_proxy.get_quota_set.return_value = quota_set
            return quota_set

        return get_quota_set

    @pytest.fixture()
    def mock_flavors(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.flavors = mocker.MagicMock()

        flavors = {"standard.small": {"vcpu": 1, "ram": 2}}
        open_stack_proxy._get_flavors_dict = mocker.MagicMock()
        open_stack_proxy._get_flavors_dict.return_value = flavors

    @pytest.fixture()
    def quotas(self):
        quota = Quota(100, 0)
        return {"vcpu": quota, "ram": quota, "instances": quota, "network": quota, "subnet": quota, "port": quota}

    @pytest.fixture()
    def expected_hardware_usage(self):
        return HardwareUsage(5, 10, 5, 4, 4, 12)

    @pytest.mark.parametrize(
        "image_parameters",
        [
            {
                "os_distro": "os_distro",
                "os_type": "os_type",
                "disk_format": "disk_format",
                "container_format": "container_format",
                "visibility": "visibility",
                "size": "size",
                "status": "status",
                "min_ram": "min_ram",
                "min_disk": "min_disk",
                "created_at": "created_at",
                "updated_at": "updated_at",
                "tags": "tags",
                "default_user": "default_user",
                "name": "image_name",
                "owner_specified.openstack.version": "4.2.0",
                "owner_specified.openstack.md5": "cdfb2e4052f2d6d97b0001ee998909a7",
                "owner_specified.openstack.sha256": "0463ac656ddbc771fda81a428b2eb",
                "owner_specified.openstack.object": "images/debian-10-4.2.0",
            }
        ],
    )
    @pytest.mark.parametrize(
        "image_object",
        [
            {
                "os_distro": "os_distro",
                "os_type": "os_type",
                "disk_format": "disk_format",
                "container_format": "container_format",
                "visibility": "visibility",
                "size": "size",
                "status": "status",
                "min_ram": "min_ram",
                "min_disk": "min_disk",
                "created_at": "created_at",
                "updated_at": "updated_at",
                "tags": "tags",
                "default_user": "default_user",
                "name": "image_name",
                "owner_specified": {
                    "owner_specified.openstack.version": "4.2.0",
                    "owner_specified.openstack.md5": "cdfb2e4052f2d6d97b0001ee998909a7",
                    "owner_specified.openstack.sha256": "0463ac656ddbc771fda81a428b2eb",
                    "owner_specified.openstack.object": "images/debian-10-4.2.0",
                },
            }
        ],
    )
    def test_image_list(self, mocker, open_stack_proxy, image_parameters, image_object):
        expected_image = Image(**image_object)

        open_stack_proxy.glance.images.list = mocker.MagicMock()
        open_stack_proxy.glance.images.list.return_value = [image_parameters]

        result = open_stack_proxy.list_images()

        assert result[0] == expected_image

    def test_get_keypair(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.keypairs.get = mocker.MagicMock()
        open_stack_proxy.nova.keypairs.get.return_value = "testKey"

        result = open_stack_proxy.get_keypair("keypair")

        assert result == "testKey"
        open_stack_proxy.nova.keypairs.get.assert_called_with("keypair")

    def test_get_keypair_nonexistent_keypair(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.keypairs.get = mocker.MagicMock()
        open_stack_proxy.nova.keypairs.get.side_effect = NovaClientException("testException")

        with pytest.raises(CrczpException):
            open_stack_proxy.get_keypair("keypair")

    def test_create_keypair(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.keypairs.create = mocker.MagicMock()
        open_stack_proxy.nova.keypairs.create.return_value = "testKeyPair"

        result = open_stack_proxy.create_keypair("keypair", "public_key", "ssh")

        assert result == "testKeyPair"
        open_stack_proxy.nova.keypairs.create.assert_called_with("keypair", "public_key", key_type="ssh")

    def test_create_keypair_already_exist(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.keypairs.create = mocker.MagicMock()
        open_stack_proxy.nova.keypairs.create.side_effect = NovaClientException("testException")

        with pytest.raises(CrczpException):
            open_stack_proxy.create_keypair("key", "public_key", "ssh")

    def test_delete_keypair(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.keypairs.delete = mocker.MagicMock()
        open_stack_proxy.nova.keypairs.delete.return_value = "keyDeleted"

        result = open_stack_proxy.delete_keypair("keypair")

        assert result == "keyDeleted"
        open_stack_proxy.nova.keypairs.delete.assert_called_with("keypair")

    def test_delete_keypair_nonexistent_keypair(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.keypairs.delete = mocker.MagicMock()
        open_stack_proxy.nova.keypairs.delete.side_effect = NovaClientException("testException")

        with pytest.raises(CrczpException):
            open_stack_proxy.delete_keypair("key")

    def test_instance_reboot(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.servers.reboot = mocker.MagicMock()
        open_stack_proxy.reboot_instance("node_id")

        open_stack_proxy.nova.servers.reboot.assert_called_with("node_id")

    def test_instance_reboot_stack_or_instance_not_found(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.servers.reboot = mocker.MagicMock()
        open_stack_proxy.nova.servers.reboot.side_effect = NovaClientException("testException")

        with pytest.raises(CrczpException):
            open_stack_proxy.reboot_instance("node_id")

    def test_instance_start(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.servers.start = mocker.MagicMock()
        open_stack_proxy.start_instance("node_id")

        open_stack_proxy.nova.servers.start.assert_called_with("node_id")

    def test_instance_start_stack_or_instance_not_found(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.servers.start = mocker.MagicMock()
        open_stack_proxy.nova.servers.start.side_effect = NovaClientException("testException")

        with pytest.raises(CrczpException):
            open_stack_proxy.start_instance("node_id")

    def test_instance_resume(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.servers.resume = mocker.MagicMock()
        open_stack_proxy.resume_instance("node_id")

        open_stack_proxy.nova.servers.resume.assert_called_with("node_id")

    def test_instance_resume_stack_or_instance_not_found(self, mocker, open_stack_proxy):
        open_stack_proxy.nova.servers.resume = mocker.MagicMock()
        open_stack_proxy.nova.servers.resume.side_effect = NovaClientException("testException")

        with pytest.raises(CrczpException):
            open_stack_proxy.resume_instance("node_id")

    def test_get_console_url(self, mocker, open_stack_proxy, stack_resources):
        open_stack_proxy.nova.servers.get_console_url = mocker.MagicMock()

        expected_console = open_stack_proxy.nova.servers.get_console_url(
            stack_resources["physical_resource_id"], "spice-html5"
        )["console"]["url"]

        result = open_stack_proxy.get_console_url("node_id", "spice-html5")

        assert result == expected_console
        open_stack_proxy.nova.servers.get_console_url.assert_called_with(
            stack_resources["physical_resource_id"], "spice-html5"
        )

    def test_get_console_url_instance_or_stack_not_found(self, mocker, open_stack_proxy, stack_resources):
        open_stack_proxy.nova.servers.get_console_url = mocker.MagicMock()
        open_stack_proxy.nova.servers.get_console_url.side_effect = NovaClientException("testException")

        with pytest.raises(StackException):
            open_stack_proxy.get_console_url("node_id", "spice-html5")

    def test_get_quota_set(self, mocker, open_stack_proxy):
        custom_quota_set = mocker.MagicMock()
        custom_quota_set.cores = {"limit": 111, "in_use": 100}
        custom_quota_set.ram = {"limit": 50500, "in_use": 1500}
        custom_quota_set.instances = {"limit": 222, "in_use": 200}

        custom_network_quotas = {
            "quota": {
                "network": {"limit": 333, "used": 300},
                "subnet": {"limit": 444, "used": 400},
                "port": {"limit": 555, "used": 500},
            }
        }

        expected_quota_set = QuotaSet(
            Quota(111, 100), Quota(50.5, 1.5), Quota(222, 200), Quota(333, 300), Quota(444, 400), Quota(555, 500)
        )

        open_stack_proxy.nova.quotas.get = mocker.MagicMock()
        open_stack_proxy.nova.quotas.get.return_value = custom_quota_set

        open_stack_proxy.neutron.show_quota_details = mocker.MagicMock()
        open_stack_proxy.neutron.show_quota_details.return_value = custom_network_quotas

        result_quota_set = open_stack_proxy.get_quota_set("tenant_id")

        assert result_quota_set == expected_quota_set

    def test_get_flavors_dict(self, mocker, open_stack_proxy):
        flavor = mocker.MagicMock()
        flavor.name = "test_flavor"
        flavor.vcpus = 1
        flavor.ram = 2000

        result = OpenStackProxy._get_flavors_dict([flavor])

        assert result.get("test_flavor")
        assert result["test_flavor"].get("vcpu") == 1
        assert result["test_flavor"].get("ram") == 2

    def test_get_hardware_usage(self, open_stack_proxy, topology_instance, mock_flavors, expected_hardware_usage):
        result = open_stack_proxy.get_hardware_usage(topology_instance)

        assert expected_hardware_usage == result

    def test_get_project_limits(self, mocker, open_stack_proxy):
        expected_limits_dict = {"vcpu": 10, "ram": 10.0, "instances": 10, "network": 10, "subnet": 10, "port": 10}

        total_cores = mocker.MagicMock()
        total_cores.name = "maxTotalCores"
        total_cores.value = expected_limits_dict["vcpu"]
        total_ram = mocker.MagicMock()
        total_ram.name = "maxTotalRAMSize"
        total_ram.value = expected_limits_dict["ram"] * 1000
        total_instance = mocker.MagicMock()
        total_instance.name = "maxTotalInstances"
        total_instance.value = expected_limits_dict["instances"]

        nova_limits = mocker.MagicMock()
        nova_limits.absolute = [total_cores, total_ram, total_instance]
        open_stack_proxy.nova.limits.get = mocker.MagicMock()
        open_stack_proxy.nova.limits.get.return_value = nova_limits

        neutron_limits = {
            "quota": {
                "network": expected_limits_dict["network"],
                "subnet": expected_limits_dict["subnet"],
                "port": expected_limits_dict["port"],
            }
        }

        open_stack_proxy.neutron.show_quota = mocker.MagicMock()
        open_stack_proxy.neutron.show_quota.return_value = neutron_limits

        result_limits = open_stack_proxy.get_project_limits("tenant_id")
        expected_limits = Limits(**expected_limits_dict)

        assert result_limits.vcpu == expected_limits.vcpu
        assert result_limits.ram == expected_limits.ram
        assert result_limits.instances == expected_limits.instances

    def test_validate_and_get_terraform_template(self, open_stack_proxy, topology_instance, generated_terraform_template):
        template_str = open_stack_proxy.validate_and_get_terraform_template(topology_instance)
        yaml = YAML(typ="rt")
        template_dict = yaml.load(template_str)

        assert ("\n".join([s for s in str(template_dict).split("\n") if s != ""])) == (str(generated_terraform_template))
