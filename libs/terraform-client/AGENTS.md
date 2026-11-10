# AGENTS.md: terraform-client

Workspace-wide rules (uv, tox, checks, branching) are in [../../AGENTS.md](../../AGENTS.md). This file covers only this package.

**Distribution:** `crczp-terraform-client` · **Import:** `crczp.terraform_driver` · **Depends on:** python-commons, openstack-lib, aws-lib · **Used by:** sandbox-service

Deploys and destroys sandbox stacks: it asks the selected cloud driver for the Terraform files of a topology instance, then runs the OpenTofu CLI (`tofu`) on them, one stack directory and workspace per sandbox. sandbox-service builds its client in `sandbox_common_lib/cloud_utils.py`.

## Module map

* `terraform_client.py`: `CrczpTerraformClient`, the entry point. `AvailableCloudLibraries` maps a cloud name to its driver class (`OPENSTACK`, `AWS`); the client also passes driver calls (images, nodes, keypairs, quotas) through to the selected driver.
* `terraform_client_manager.py`: `CrczpTerraformClientManager`, which runs `tofu` through `subprocess` with argument lists, never a shell (`init`, `plan`, `apply`, `destroy`, `workspace`, `state pull`, `force-unlock`), and manages the stack directories.
* `terraform_backend.py`, `templates/terraform_backend.j2`: `CrczpTerraformBackend`, the state backend block (`CrczpTerraformBackendType` in `terraform_client_elements.py`).
* `terraform_exceptions.py`, `terraform_exc_handlers.py`: the Terraform exceptions (`TerraformStateLocked` is caught by sandbox-service) and the handlers that raise them from a failed `tofu` command, including the parsing of a state-lock ID.

## Package notes

* The tests mock the subprocess calls; nothing here needs `tofu` installed. The image installs it (see `sandbox-service/Dockerfile`).
