# The version constraint must match libs/openstack-lib/crczp/openstack_driver/templates/terraform-provider-template.j2.
terraform {
  required_providers {
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = "~> 3.4"
    }
  }
}

provider "openstack" {}
