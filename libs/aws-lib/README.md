# cyberrangecz-aws-lib

> **Note:** Development of this library has moved to the
> [backend-sandbox-platform](https://github.com/cyberrangecz/backend-sandbox-platform)
> monorepo, where it is maintained alongside `sandbox-service` as a `uv`
> workspace member. It is no longer released to PyPI; the versions already
> published there are frozen.

Python library that serves as AWS driver for the sandbox-service (the Django microservice).
It is meant to be an installable component, not stand-alone library.

It implements the
[CyberrangeczCloudClientBase](https://github.com/cyberrangecz/backend-aws-lib)
interface. The interface allows seemless integration to the Cyberrangecz platform environment.

## Communication with AWS API
The library utilizes Boto3 library for communication with AWS API. Boto3 implements multiple clients,
each serving its own AWS Service.

## Contents
This library contains:
 * **crczp/aws_driver** -- the implementation of the library
 * **pyproject.toml** -- metadata of the library
