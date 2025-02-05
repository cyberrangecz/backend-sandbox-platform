import pytest

from crczp.cloud_commons import CrczpException


def arguments_for_wait_for_test():
    arguments = [{"stack_id": "stack_id", "poll_period": 50, "out": "out"}, {"stack_id": "stack_id"}]

    for arg in arguments:
        yield arg


class TestOpenStackClient:
    def test_list_images(self, mocker, os_client):
        os_client.open_stack_proxy.list_images = mocker.MagicMock()
        os_client.open_stack_proxy.list_images.return_value = "imageList"

        result = os_client.list_images()

        assert result == "imageList"

    def test_create_terraform_template(self, mocker, os_client, topology_instance):
        os_client.open_stack_proxy.validate_and_get_terraform_template = mocker.MagicMock()
        os_client.open_stack_proxy.validate_and_get_terraform_template.return_value = "template"

        result = os_client.create_terraform_template(topology_instance)
        assert result == "template"

    def test_get_image(self, mocker, os_client):
        os_client.open_stack_proxy.get_image = mocker.MagicMock()
        os_client.open_stack_proxy.get_image.return_value = "image"

        result = os_client.get_image("image_id")
        assert result == "image"
        os_client.open_stack_proxy.get_image.assert_called_with("image_id")

    def test_resume_node(self, mocker, os_client):
        os_client.open_stack_proxy.resume_instance = mocker.MagicMock()
        os_client.resume_node("node_id")

        os_client.open_stack_proxy.resume_instance.assert_called_with("node_id")

    def test_resume_node_not_found(self, mocker, os_client):
        os_client.open_stack_proxy.resume_instance = mocker.MagicMock()
        os_client.open_stack_proxy.resume_instance.side_effect = CrczpException("testException")

        with pytest.raises(CrczpException):
            os_client.resume_node("node_id")

    def test_start_node(self, mocker, os_client):
        os_client.open_stack_proxy.start_instance = mocker.MagicMock()
        os_client.start_node("node_id")

        os_client.open_stack_proxy.start_instance.assert_called_with("node_id")

    def test_start_node_not_found(self, mocker, os_client):
        os_client.open_stack_proxy.start_instance = mocker.MagicMock()
        os_client.open_stack_proxy.start_instance.side_effect = CrczpException("textException")

        with pytest.raises(CrczpException):
            os_client.start_node("node_id")

    def test_reboot_node(self, mocker, os_client):
        os_client.open_stack_proxy.reboot_instance = mocker.MagicMock()
        os_client.reboot_node("node_id")

        os_client.open_stack_proxy.reboot_instance.assert_called_with("node_id")

    def test_reboot_node_not_found(self, mocker, os_client):
        os_client.open_stack_proxy.reboot_instance = mocker.MagicMock()
        os_client.open_stack_proxy.reboot_instance.side_effect = CrczpException("testException")

        with pytest.raises(CrczpException):
            os_client.reboot_node("node_id")

    def test_get_console_url(self, mocker, os_client):
        os_client.open_stack_proxy.get_console_url = mocker.MagicMock()
        os_client.open_stack_proxy.get_console_url.return_value = "spiceConsole"

        result = os_client.get_console_url("node_id", "spice-html5")

        assert result == "spiceConsole"

    def test_get_console_url_not_found(self, mocker, os_client):
        os_client.open_stack_proxy.get_console_url = mocker.MagicMock()
        os_client.open_stack_proxy.get_console_url.side_effect = CrczpException("testException")

        with pytest.raises(CrczpException):
            os_client.get_console_url("node_id", "spice-html5")

    @pytest.mark.parametrize(
        "arguments",
        [
            {
                "name": "name",
                "public_key": "key",
                "key_type": "x509",
            },
            {"name": "name", "public_key": "key"},
            {"name": "name"},
        ],
    )
    def test_create_keypair(self, mocker, os_client, arguments):
        os_client.open_stack_proxy.create_keypair = mocker.MagicMock()
        os_client.create_keypair(**arguments)

        os_client.open_stack_proxy.create_keypair.assert_called_with(
            arguments["name"], arguments.get("public_key"), arguments.get("key_type", "ssh")
        )

    def test_create_keypair_create_failed(self, mocker, os_client):
        os_client.open_stack_proxy.create_keypair = mocker.MagicMock()
        os_client.open_stack_proxy.create_keypair.side_effect = CrczpException("testException")

        with pytest.raises(CrczpException):
            os_client.create_keypair("key")

    def test_get_keypair(self, mocker, os_client):
        os_client.open_stack_proxy.get_keypair = mocker.MagicMock()
        os_client.open_stack_proxy.get_keypair.return_value = "keypair"

        result = os_client.get_keypair("keypair")

        assert result == "keypair"

    def test_get_keypair_nonexisting_keypair(self, mocker, os_client):
        os_client.open_stack_proxy.get_keypair = mocker.MagicMock()
        os_client.open_stack_proxy.get_keypair.side_effect = CrczpException("testException")

        with pytest.raises(CrczpException):
            os_client.get_keypair("keypair")

    def test_delete_keypair(self, mocker, os_client):
        os_client.open_stack_proxy.delete_keypair = mocker.MagicMock()
        os_client.delete_keypair("name")

        os_client.open_stack_proxy.delete_keypair.assert_called_with("name")

    def test_delete_keypair_not_found(self, mocker, os_client):
        os_client.open_stack_proxy.delete_keypair = mocker.MagicMock()
        os_client.open_stack_proxy.delete_keypair.side_effect = CrczpException("testException")

        with pytest.raises(CrczpException):
            os_client.delete_keypair("key")

    def test_get_project_name(self, mocker, os_client):
        auth_ref = mocker.MagicMock()
        auth_ref.project_name = "project_name"

        os_client.session.auth.get_auth_ref = mocker.MagicMock()
        os_client.session.auth.get_auth_ref.return_value = auth_ref

        result_project_name = os_client.get_project_name()

        assert result_project_name == "project_name"

    def test_get_quota_set(self, mocker, os_client):
        os_client.get_quota_set = mocker.MagicMock()
        os_client.get_quota_set.return_value = "quota_set"

        result = os_client.get_quota_set()

        assert result == "quota_set"

    def test_get_hardware_usage(self, mocker, os_client, topology_instance):
        os_client.open_stack_proxy.get_hardware_usage = mocker.MagicMock()
        os_client.open_stack_proxy.get_hardware_usage.return_value = "hardwareUsage"

        result = os_client.get_hardware_usage(topology_instance)
        assert result == "hardwareUsage"

    def test_get_project_limits(self, mocker, os_client):
        os_client.session = mocker.MagicMock()
        os_client.open_stack_proxy.get_project_limits = mocker.MagicMock()
        os_client.open_stack_proxy.get_project_limits.return_value = "projectLimits"

        result = os_client.get_project_limits()

        assert result == "projectLimits"
