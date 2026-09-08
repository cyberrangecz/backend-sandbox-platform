# Openstack cloud driver
This repository hosts Openstack cloud libraries that are imlemented for CyberRangeCZ Platform.

## Modules

It consists of the following modules.

* ostack_client - a client that provides all necessary functions for heat stack manipulation
* utils - some common functions
* exceptions - used exceptions

## Releasing a new version
The release of a new version consists of two steps:
 1. Update the version of package in the pyproject.toml file. Note that upload of the package will fail
 if the registry already contains the package with given name and version.
