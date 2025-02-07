# cyberrangecz-aws-lib

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
