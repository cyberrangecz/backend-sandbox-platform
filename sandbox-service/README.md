# Content

- [Content](#content)
- [Sandbox Service](#sandbox-service)
  - [Project Modules](#project-modules)
  - [Trainee Sandboxes and Pool Maintenance](#trainee-sandboxes-and-pool-maintenance)
  - [Tests](#tests)
  - [Wiki](#wiki)
  - [Deployment](#deployment)

# Sandbox Service

This project simplifies manipulation of cloud platforms (OpenStack and AWS) for CyberRangeCZ Platform purposes. It is a Django REST Framework service whose API is documented via OpenAPI (drf-spectacular).

It provides REST calls for manipulation with:

* Sandbox definitions
* Pools of sandboxes
* Sandboxes themselves
* Applying Ansible playbooks on sandbox machines
* OpenStack project quota, image, and limit information

Provisioning is backed by Terraform (`crczp-terraform-client`), with a client selected per deployment via `crczp-openstack-lib` or `crczp-aws-lib` depending on which cloud is configured.

## Project Modules
This Django project is organized as one project-wide settings module, four self-contained apps, and two shared libraries.
- __Sandbox Common Lib__ with shared configuration, exceptions, permissions, and cloud/utility helpers (including OpenStack/AWS client selection)
- __Sandbox UAG__ handling OIDC/JWT authentication and role-based permissions
- __Sandbox Definition App__ which handles the sandbox definitions (including Git-based definition providers)
- __Sandbox Ansible App__ which runs Ansible on the sandbox
- __Sandbox Instance App__ which manages the sandboxes, pools, and their lifecycle requests
- __Sandbox Cloud App__ which exposes OpenStack project quota/image/limit information
- __Sandbox Service Project__ with the Django project settings, root URL configuration, and WSGI entrypoint

## Trainee Sandboxes and Pool Maintenance
Trainings that do not pre-allocate a pool can let each trainee allocate a sandbox for themselves. This needs `authenticated_rest_api: true`, because the sandbox belongs to the authenticated caller:

* `POST /pools/{pool_id}/sandbox-allocation-units` with the header `X-Training-Access-Token: <access token of the training holding the pool>` builds one sandbox for the caller (`count` must be 1 or left out). A caller may hold one active sandbox per pool; another request answers 409.
* The owner may `GET` the unit (`/sandbox-allocation-units/{id}`), `GET` and `POST` its `cleanup-request`, `DELETE` its `lock`, and list their units with `GET /sandbox-allocation-units/by-creator?created_by_sub=<own sub>`, adding `&state=ACTIVE` for the ones that hold or are building a sandbox. Everything else still needs the organizer's model permissions.
* `get-and-lock` and "delete unlocked" (`cleanup-unlocked`) leave trainee-owned sandboxes alone.
* `python manage.py cleanup_trainee_sandboxes` cleans up trainee sandboxes older than `trainee_sandbox_cleanup.max_age_hours` (24 by default). It does nothing unless `trainee_sandbox_cleanup.enabled` is set, and runs only when the deployment schedules it (see [Deployment](#deployment)).

When a pool's units get stuck, three actions remove them:

* `POST /pools/{pool_id}/cancel-queued` (organizer or admin) cancels the allocations that have not started yet and removes their units.
* `POST /pools/{pool_id}/force-cancel-allocation` and `POST /pools/{pool_id}/force-cleanup` (admin only) cancel allocations whose first stage hangs, or cleanups that hang, and remove the units from the database. What those units already have in the cloud stays there and must be removed by hand.

Forced pool cleanups skip the units that cannot be cleaned up yet and answer with their ids (`skipped_unit_ids`).

## Tests

| File | Type | Covers |
|------|------|--------|
| `sandbox_common_lib/tests/test_utils.py` | Unit | Utility functions |
| `sandbox_common_lib/tests/test_crczp_config.py` | Unit | CRCZP configuration parsing |
| `sandbox_common_lib/tests/test_crczp_config_validation.py` | Unit | Configuration attribute validation |
| `sandbox_common_lib/tests/test_netbird_client.py` | Unit | NetBird management API client |
| `sandbox_common_lib/tests/test_pagination.py` | Unit | Sorting of paginated lists |
| `sandbox_common_lib/tests/test_exc_handler.py` | Unit | Status codes of API errors |
| `sandbox_definition_app/tests/test_definitions.py` | Unit | Create/load/get definitions, topology validation |
| `sandbox_definition_app/tests/test_definition_providers.py` | Unit | GitLab/GitHub provider URL parsing, ref fetching |
| `sandbox_definition_app/tests/test_definition_providers.py` (`TestGitIntegration`) | **Integration** | Live Git operations |
| `sandbox_instance_app/tests/test_topology.py` | Unit | Topology serialization, Docker container handling |
| `sandbox_instance_app/tests/test_nodes.py` | Unit | Node actions, node retrieval |
| `sandbox_instance_app/tests/test_projects.py` | Unit | Project management |
| `sandbox_instance_app/tests/test_pools.py` | Unit | Sandbox pool operations |
| `sandbox_instance_app/tests/test_sandboxes.py` | Unit | Sandbox lifecycle |
| `sandbox_instance_app/tests/test_requests.py` | Unit | Request handling |
| `sandbox_instance_app/tests/test_request_handlers.py` | Unit | Request handler logic |
| `sandbox_instance_app/tests/test_request_handlers.py` (`TestAllocationRequestHandler`, `TestCleanupRequestHandler`) | **Integration** | Allocation/cleanup request handling |
| `sandbox_instance_app/tests/test_stage_handlers.py` | Unit | Stage handler logic |
| `sandbox_instance_app/tests/test_sshconfig.py` | Unit | SSH config generation |
| `sandbox_instance_app/tests/test_flavor_mapping.py` | Unit | Flavor mapping |
| `sandbox_instance_app/tests/test_netbird.py` | Unit | NetBird provisioning, teardown, sandbox VPN API view |
| `sandbox_instance_app/tests/test_jump_proxy_cleanup.py` | Unit | SSH key removal on the jump proxy |
| `sandbox_instance_app/tests/test_allocation_units.py` | Unit | Allocation unit API views, trainee self-allocation and access |
| `sandbox_instance_app/tests/test_permissions.py` | Unit | Trainee self-service permissions |
| `sandbox_instance_app/tests/test_unit_filters.py` | Unit | Allocation unit state filters |
| `sandbox_instance_app/tests/test_pool_maintenance.py` | Unit | Removing stuck allocation units |
| `sandbox_instance_app/tests/test_trainee_cleanup.py` | Unit | Cleanup of trainee sandboxes and its command |
| `sandbox_ansible_app/tests/test_ansible.py` | Unit | Ansible execution |
| `sandbox_ansible_app/tests/test_inventory.py` | Unit | Ansible inventory building |
| `sandbox_ansible_app/tests/test_stages.py` | Unit | Ansible stage handling |
| `sandbox_service_project/tests/test_integration.py` | **Integration** | Full service integration |

 Integration tests are marked with `@pytest.mark.integration` and are included in the default `tox` run.
 To run only the integration tests explicitly, use `pytest -m integration`.
 
## Wiki
The wiki with documentation to this project: [Sandbox Service wiki](https://github.com/cyberrangecz/backend-sandbox-service/wiki)

## Deployment
CI/CD runs via GitHub Actions (`.github/workflows/github-actions.yml`):

* Every push to a non-`master` branch and every pull request against `master` runs the full quality suite: pre-commit (ruff, including its security rules, and ty), pylint and pytest through `tox`, a dependency audit, and a secret scan of the new commits (gitleaks and TruffleHog).
* The dependency audit fails a branch only for vulnerabilities that branch introduces. Vulnerabilities already on `master` are reported but not blocking, and are fixed in their own PR to `master`.
* Pushes also build a Docker image, push it to the GitHub Container Registry untagged (by digest), scan it with grype (report only), and then tag it `ghcr.io/cyberrangecz/backend-sandbox-platform/sandbox-service:v<version>-dev`.
* Manually dispatching the workflow on `master` with `confirm_action` set to `yes` does the same with the release tag `v<version>` from `pyproject.toml`, generates the OpenAPI schema (drf-spectacular) to GitHub Pages, and pushes the corresponding git tag. Without `yes`, or with a known dependency vulnerability not listed in `audit-ignore.txt`, the build still runs but nothing is released: the image is tagged `v<version>-staging` only, for testing, with no release tag, no docs and no git tag. The grype image scan stays report-only here too: a high/critical finding that has a fix raises a warning on the run but does not stop the release. The image carries an SBOM and build provenance as attestations.
* `.github/workflows/security-schedule.yml` rescans `master` twice a week (Monday and Thursday): dependencies, the latest released image, and the full git history (TruffleHog). It opens an issue for each failing scan.

At container startup (`bin/run-sandbox-service.sh`), the service applies database migrations, creates the cache table, creates/refreshes a Django admin account from the `DJANGO_ADMIN_USER`, `DJANGO_ADMIN_EMAIL`, and `DJANGO_ADMIN_PASSWORD` environment variables, registers roles, and starts `gunicorn`.

Nothing in the image cleans up the sandboxes trainees allocate for themselves. To clean them up, set `trainee_sandbox_cleanup.enabled: true` and schedule `python manage.py cleanup_trainee_sandboxes` with the service's image and configuration, e.g. as an hourly Kubernetes CronJob. The command exits non-zero when a sandbox could not be cleaned up.
