data "openstack_networking_network_v2" "crczp-network" {
  name = "crczp-network"
}

data "openstack_networking_secgroup_v2" "sandbox-access-sg" {
  name = "sandbox-access-sg"
}

data "openstack_networking_secgroup_v2" "sandbox-man-sg" {
  name = "sandbox-man-sg"
}

data "openstack_networking_secgroup_v2" "sandbox-internal-sg" {
  name = "sandbox-internal-sg"
}

data "openstack_networking_secgroup_v2" "sandbox-man-int-sg" {
  name = "sandbox-man-int-sg"
}

resource "openstack_networking_network_v2" "stack-name-server-switch" {
  name = "stack-name-server-switch"
  admin_state_up = "true"
}

resource "openstack_networking_subnet_v2" "stack-name-server-switch-subnet" {
  name = "stack-name-server-switch-subnet"
  network_id = openstack_networking_network_v2.stack-name-server-switch.id
  cidr = "10.10.20.0/24"
  gateway_ip = "10.10.20.1"
  dns_nameservers = [
  ]
}

resource "openstack_networking_port_v2" "stack-name-link-5" {
  name = "stack-name-link-5"
  network_id = openstack_networking_network_v2.stack-name-server-switch.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-internal-sg.id
  ]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.stack-name-server-switch-subnet.id
    ip_address = "10.10.20.5"
  }

  allowed_address_pairs {
    ip_address = openstack_networking_subnet_v2.stack-name-server-switch-subnet.cidr
  }
}

resource "openstack_networking_port_v2" "stack-name-link-11" {
  name = "stack-name-link-11"
  network_id = openstack_networking_network_v2.stack-name-server-switch.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-internal-sg.id
  ]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.stack-name-server-switch-subnet.id
    ip_address = "10.10.20.1"
  }

  allowed_address_pairs {
    ip_address = "0.0.0.0/0"
  }
}

resource "openstack_networking_network_v2" "stack-name-home-switch" {
  name = "stack-name-home-switch"
  admin_state_up = "true"
}

resource "openstack_networking_subnet_v2" "stack-name-home-switch-subnet" {
  name = "stack-name-home-switch-subnet"
  network_id = openstack_networking_network_v2.stack-name-home-switch.id
  cidr = "10.10.30.0/24"
  gateway_ip = "10.10.30.1"
  dns_nameservers = [
  ]
}

resource "openstack_networking_port_v2" "stack-name-link-6" {
  name = "stack-name-link-6"
  network_id = openstack_networking_network_v2.stack-name-home-switch.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-internal-sg.id
  ]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.stack-name-home-switch-subnet.id
    ip_address = "10.10.30.5"
  }

  allowed_address_pairs {
    ip_address = openstack_networking_subnet_v2.stack-name-home-switch-subnet.cidr
  }
}

resource "openstack_networking_port_v2" "stack-name-link-12" {
  name = "stack-name-link-12"
  network_id = openstack_networking_network_v2.stack-name-home-switch.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-internal-sg.id
  ]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.stack-name-home-switch-subnet.id
    ip_address = "10.10.30.1"
  }

  allowed_address_pairs {
    ip_address = "0.0.0.0/0"
  }
}

resource "openstack_networking_network_v2" "stack-name-wan" {
  name = "stack-name-wan"
  admin_state_up = "true"
}

resource "openstack_networking_subnet_v2" "stack-name-wan-subnet" {
  name = "stack-name-wan-subnet"
  network_id = openstack_networking_network_v2.stack-name-wan.id
  cidr = "100.100.100.0/24"
  no_gateway = "true"
  dns_nameservers = [
  ]
}

resource "openstack_networking_port_v2" "stack-name-link-2" {
  name = "stack-name-link-2"
  network_id = openstack_networking_network_v2.stack-name-wan.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-internal-sg.id
  ]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.stack-name-wan-subnet.id
  }

  allowed_address_pairs {
    ip_address = "0.0.0.0/0"
  }
}

resource "openstack_networking_port_v2" "stack-name-link-8" {
  name = "stack-name-link-8"
  network_id = openstack_networking_network_v2.stack-name-wan.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-internal-sg.id
  ]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.stack-name-wan-subnet.id
  }

  allowed_address_pairs {
    ip_address = "0.0.0.0/0"
  }
}

resource "openstack_networking_port_v2" "stack-name-link-10" {
  name = "stack-name-link-10"
  network_id = openstack_networking_network_v2.stack-name-wan.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-internal-sg.id
  ]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.stack-name-wan-subnet.id
  }

  allowed_address_pairs {
    ip_address = "0.0.0.0/0"
  }
}

resource "openstack_networking_network_v2" "stack-name-man-network" {
  name = "stack-name-man-network"
  admin_state_up = "true"
}

resource "openstack_networking_subnet_v2" "stack-name-man-network-subnet" {
  name = "stack-name-man-network-subnet"
  network_id = openstack_networking_network_v2.stack-name-man-network.id
  cidr = "192.168.128.0/17"
  no_gateway = "true"
  dns_nameservers = [
  ]
}

resource "openstack_networking_port_v2" "stack-name-link-1" {
  name = "stack-name-link-1"
  network_id = openstack_networking_network_v2.stack-name-man-network.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-man-sg.id
  ]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.stack-name-man-network-subnet.id
  }

  allowed_address_pairs {
    ip_address = openstack_networking_subnet_v2.stack-name-man-network-subnet.cidr
  }
}

resource "openstack_networking_port_v2" "stack-name-link-3" {
  name = "stack-name-link-3"
  network_id = openstack_networking_network_v2.stack-name-man-network.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-man-int-sg.id
  ]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.stack-name-man-network-subnet.id
  }

  allowed_address_pairs {
    ip_address = openstack_networking_subnet_v2.stack-name-man-network-subnet.cidr
  }
}

resource "openstack_networking_port_v2" "stack-name-link-4" {
  name = "stack-name-link-4"
  network_id = openstack_networking_network_v2.stack-name-man-network.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-man-int-sg.id
  ]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.stack-name-man-network-subnet.id
  }

  allowed_address_pairs {
    ip_address = openstack_networking_subnet_v2.stack-name-man-network-subnet.cidr
  }
}

resource "openstack_networking_port_v2" "stack-name-link-7" {
  name = "stack-name-link-7"
  network_id = openstack_networking_network_v2.stack-name-man-network.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-man-int-sg.id
  ]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.stack-name-man-network-subnet.id
  }

  allowed_address_pairs {
    ip_address = openstack_networking_subnet_v2.stack-name-man-network-subnet.cidr
  }
}

resource "openstack_networking_port_v2" "stack-name-link-9" {
  name = "stack-name-link-9"
  network_id = openstack_networking_network_v2.stack-name-man-network.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-man-int-sg.id
  ]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.stack-name-man-network-subnet.id
  }

  allowed_address_pairs {
    ip_address = openstack_networking_subnet_v2.stack-name-man-network-subnet.cidr
  }
}

data "openstack_images_image_ids_v2" "image_data_source-stack-name-server" {
  name = "debian-12-x86_64"
}

resource "openstack_compute_instance_v2" "stack-name-server" {
  name = "stack-name-server"
  image_name = "debian-12-x86_64"
  flavor_name = "standard.small"
  key_pair = "dummy-ssh-key-pair"
  config_drive = "true"

  network {
    port = openstack_networking_port_v2.stack-name-link-3.id
  }

  network {
    port = openstack_networking_port_v2.stack-name-link-5.id
  }
}

data "openstack_images_image_ids_v2" "image_data_source-stack-name-home" {
  name = "debian-12-x86_64"
}

resource "openstack_compute_instance_v2" "stack-name-home" {
  name = "stack-name-home"
  image_name = "debian-12-x86_64"
  flavor_name = "standard.small"
  key_pair = "dummy-ssh-key-pair"
  config_drive = "true"

  network {
    port = openstack_networking_port_v2.stack-name-link-4.id
  }

  network {
    port = openstack_networking_port_v2.stack-name-link-6.id
  }
}

data "openstack_images_image_ids_v2" "image_data_source-stack-name-server-router" {
  name = "debian-12-x86_64"
}

resource "openstack_compute_instance_v2" "stack-name-server-router" {
  name = "stack-name-server-router"
  image_name = "debian-12-x86_64"
  flavor_name = "standard.small"
  key_pair = "dummy-ssh-key-pair"
  config_drive = "true"

  network {
    port = openstack_networking_port_v2.stack-name-link-7.id
  }

  network {
    port = openstack_networking_port_v2.stack-name-link-8.id
  }

  network {
    port = openstack_networking_port_v2.stack-name-link-11.id
  }
}

data "openstack_images_image_ids_v2" "image_data_source-stack-name-home-router" {
  name = "debian-12-x86_64"
}

resource "openstack_compute_instance_v2" "stack-name-home-router" {
  name = "stack-name-home-router"
  image_name = "debian-12-x86_64"
  flavor_name = "standard.small"
  key_pair = "dummy-ssh-key-pair"
  config_drive = "true"

  network {
    port = openstack_networking_port_v2.stack-name-link-9.id
  }

  network {
    port = openstack_networking_port_v2.stack-name-link-10.id
  }

  network {
    port = openstack_networking_port_v2.stack-name-link-12.id
  }
}

resource "openstack_networking_port_v2" "stack-name-man-out-port" {
  name = "stack-name-man-out-port"
  network_id = data.openstack_networking_network_v2.crczp-network.id
  admin_state_up = "true"
  security_group_ids = [
    data.openstack_networking_secgroup_v2.sandbox-access-sg.id
  ]

  allowed_address_pairs {
    ip_address = "0.0.0.0/0"
  }
}

data "openstack_images_image_ids_v2" "image_data_source-stack-name-man" {
  name = "debian-12-x86_64"
}

resource "openstack_compute_instance_v2" "stack-name-man" {
  name = "stack-name-man"
  image_name = "debian-12-x86_64"
  flavor_name = "standard.small"
  key_pair = "dummy-ssh-key-pair"
  config_drive = "true"

  network {
    port = openstack_networking_port_v2.stack-name-man-out-port.id
  }

  network {
    port = openstack_networking_port_v2.stack-name-link-1.id
  }

  network {
    port = openstack_networking_port_v2.stack-name-link-2.id
  }
}
