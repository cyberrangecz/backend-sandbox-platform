# Openstack cloud driver

> **Note:** Development of this library has moved to the
> [backend-sandbox-platform](https://github.com/cyberrangecz/backend-sandbox-platform)
> monorepo, where it is maintained alongside `sandbox-service` as a `uv`
> workspace member. It is no longer released to PyPI; the versions already
> published there are frozen.

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
