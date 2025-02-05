# Openstack cloud driver
This repository hosts Openstack cloud libraries that are imlemented for CyberRangeCZ Platform.

## Modules

It consists of the following modules.

* ostack_client - a client that provides all necessary functions for heat stack manipulation
* utils - some common functions
* exceptions - used exceptions

## Developing
The `poetry` tool is used as the package manager. Checkout the poetry [doc](https://python-poetry.org/docs/) 
for more information.

### Creating virtual environment
 1. Create a poetry environment and install dependencies
 ```bash
poetry install
```
 2. Activate the environment
 ```bash
poetry shell
```
 3. Run tests
 ```bash
python run tox
```

## Releasing a new version
The release of a new version consists of two steps:
 1. Update the version of package in the pyproject.toml file. Note that upload of the package will fail
 if the registry already contains the package with given name and version.
 2. Create a suitable commit that must include `[release]` in the commit message. For example:

 ```text
feat: implement my new special feature

<body of the commit> ... and updating version in pyproject.toml
[release]
```
