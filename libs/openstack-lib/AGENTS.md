# AGENTS.md: openstack-lib

Workspace-wide rules (uv, tox, checks, branching) are in [../../AGENTS.md](../../AGENTS.md). This file covers only this package.

**Distribution:** `crczp-openstack-lib` · **Import:** `crczp.openstack_driver` · **Depends on:** python-commons, topology-definition · **Used by:** terraform-client, sandbox-service

The OpenStack cloud driver: implements `CrczpCloudClientBase` from python-commons, so terraform-client can deploy a topology instance to OpenStack. Cloud API calls go through `openstacksdk`.

## Module map

* `open_stack_client.py`: `CrczpOpenStackClient`, the entry point and the only export. It implements the driver interface by delegating to the proxy.
* `open_stack_proxy.py`: `OpenStackProxy`, which makes the openstacksdk calls (images, servers, keypairs, quotas, flavors, stack resources) and renders the Terraform templates.
* `templates/terraform-provider-template.j2`, `templates/terraform-deploy-template.j2`: the Terraform provider block and the deployment of a topology instance (instances, networks, subnets, routers, floating IPs, tap mirrors).
* `network_forwarding.py`: tap mirrors for a topology's network forwarding (`TapMirror`, `build_tap_mirrors`).
* `utils.py`, `decorators.py`: authenticated session/connection setup and the `check_authentication` decorator.

## Package notes

* Ruff ignores `S311` here: `random` is used for non-cryptographic values only.
* `sandbox.yaml`, `small-sandbox.yaml` and `docs/` are reference material, not used by the code or the tests.
