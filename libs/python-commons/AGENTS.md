# AGENTS.md: python-commons

Workspace-wide rules (uv, tox, checks, branching) are in [../../AGENTS.md](../../AGENTS.md). This file covers only this package.

**Distribution:** `crczp-python-commons` · **Import:** `crczp.cloud_commons` · **Depends on:** topology-definition · **Used by:** openstack-lib, aws-lib, terraform-client, sandbox-service

The cloud-agnostic layer between a topology definition and a concrete cloud: the interface every cloud driver implements, the deployed form of a topology, and how cloud resources are named and cleaned up per stack. The public API is re-exported from `crczp/cloud_commons/__init__.py`.

## Module map

* `cloud_client_base.py`: `CrczpCloudClientBase`, the abstract driver interface (Terraform provider/template rendering, images, nodes, keypairs, quotas, stack inventory and deletion). openstack-lib and aws-lib implement it; changing a method signature means changing both drivers.
* `topology_instance.py`: `TopologyInstance`, a topology definition combined with a `TransformationConfiguration` (`transformation_configuration.py`: base network, management node image and flavor, DNS servers). It answers the questions the drivers and sandbox-service ask about nodes, networks, links and role-based access. Also `UNIVERSAL_ROLES`.
* `cloud_stacks.py`: `StackNameScheme`, cloud resources grouped by the stack that created them, and their ordered deletion.
* `topology_elements.py`, `cloud_client_elements.py`: value objects for the instance (`MAN`, `Link`, security groups) and for driver results (`Image`, `Quota`, `NodeDetails`, …).
* `exceptions.py`: `CrczpException` and the stack exceptions every driver raises.
* `hcl.py`: quoting of strings for generated Terraform (HCL).

## Package notes

* Ruff ignores `S311` here: `random` is used for non-cryptographic values only.
