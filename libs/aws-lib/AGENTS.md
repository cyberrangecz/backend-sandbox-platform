# AGENTS.md: aws-lib

Workspace-wide rules (uv, tox, checks, branching) are in [../../AGENTS.md](../../AGENTS.md). This file covers only this package.

**Distribution:** `crczp-aws-lib` · **Import:** `crczp.aws_driver` · **Depends on:** python-commons, topology-definition · **Used by:** terraform-client, sandbox-service

The AWS cloud driver: implements `CrczpCloudClientBase` from python-commons, so terraform-client can deploy a topology instance to AWS. Cloud API calls go through `boto3`.

## Module map

* `aws_client.py`: `CrczpAwsClient`, the entry point. It implements the driver interface: renders the Terraform templates and queries the account (images, instances, keypairs, quotas) through boto3.
* `templates/terraform-provider-template.j2`, `templates/terraform-deploy-template.j2`, `templates/security-groups-template.j2`: the Terraform provider block, the deployment of a topology instance, and its security groups, rendered by `CrczpAwsClient`.
* `stack_resources.py`: `AwsStackResources`, which lists and deletes a stack's resources through the EC2 API.
* `network_forwarding.py`: network forwarding as AWS VPC Traffic Mirroring: validates the rules against AWS limits (mirroring-capable instance families, sources per target) and maps directions to filter rules.
* `exceptions.py`: `CrczpAwsClientException` and its subclasses.
