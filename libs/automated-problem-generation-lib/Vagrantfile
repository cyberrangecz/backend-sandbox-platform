# Generated vagrant file
#
# -*- mode: ruby -*-
# vi: set ft=ruby :

#config.vm.provision :shell, :inline => "python3 generator.py"
system('python3 ./generator.py')
Vagrant.configure("2") do |config|

  # device: server
  config.vm.define "server" do |device|
    device.vm.hostname = "server"
    device.vm.box = "ubuntu/xenial64"
    device.vm.network :private_network, ip: "10.10.20.5", virtualbox__intnet: "server-switch", netmask: "24"
    device.vm.provider "virtualbox" do |vb|
      vb.cpus = 2
      vb.memory = 4096
    end
  end

  # device: home
  config.vm.define "home" do |device|
    device.vm.hostname = "home"
    device.vm.box = "ubuntu/xenial64"
    device.vm.boot_timeout = 1000
    device.vm.box_check_update = false
    device.vm.network :private_network, ip: "10.10.30.5", virtualbox__intnet: "home-switch", netmask: "24"
  end

  # device: router
  config.vm.define "router" do |device|
    device.vm.network :private_network, ip: "10.10.20.1", virtualbox__intnet: "server-switch", netmask: "24"
    device.vm.network :private_network, ip: "10.10.30.1", virtualbox__intnet: "home-switch", netmask: "24"
    device.vm.network :private_network, ip: "172.18.0.5", virtualbox__intnet: "BR", netmask: "24"
    device.vm.hostname = "router"
    device.vm.box = "generic/debian10"
    device.vm.provider "virtualbox" do |vb|
      vb.memory = 256
      vb.cpus = 1
    end
  end

  # device: br
  config.vm.define "br" do |device|
    device.vm.network :private_network, ip: "172.18.0.1", virtualbox__intnet: "BR"
    device.vm.network :public_network, ip: " 172.18.10.1", netmask: "24"
    device.vm.hostname = "br"
    device.vm.box = "generic/debian10"
    device.vm.provider "virtualbox" do |vb|
      vb.memory = 256
      vb.cpus = 1
    end
  end

  # basic ansible configuration of devices and networks
  config.vm.provision :ansible do |ansible|
    ansible.playbook = "base_provisioning/device_configuration.yml"
    ansible.verbose = true
    ansible.extra_vars = {
      ansible_python_interpreter: "/usr/bin/python3",
    }
  end


  # user configuration of devices with ansible
  config.vm.provision :ansible do |ansible|
    ansible.playbook = "provisioning/playbook.yml"
    ansible.verbose = true
    ansible.extra_vars = {
      ansible_python_interpreter: "/usr/bin/python3",
    }
  end

  
end