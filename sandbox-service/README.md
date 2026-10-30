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

When a pool's units get stuck, an organizer or admin can cancel them:

* `POST /pools/{pool_id}/cancel-allocations` (organizer or admin) removes the queued units, stops the running builds and queues their cleanup. Ready sandboxes and units being cleaned up are left alone. It answers `201` with `{"units": [...]}`, one entry per affected unit with `unit_id`, `stack_name`, `action` (`removed` for a queued unit, `stopped` for a running build, `skipped` when the allocation finished meanwhile, `failed`), `stop_pending` (the stop is not confirmed yet, so no cleanup is queued) and `reason`.

Forced pool cleanups skip the units that cannot be cleaned up yet and answer with their ids (`skipped_unit_ids`).

### Cleanup

A cleanup runs three stages in order: user Ansible, networking Ansible and stack. The last stage ends by removing the allocation unit, decrementing the pool size and, when the cleanup asks for it and the last unit is gone, deleting the pool; no separate job does it.

* The stack stage destroys the stack through its OpenTofu state. After a failed destroy, or when the stack has no state, it deletes every cloud resource (OpenStack and AWS) whose name is canonical for the stack, or that carries the stack-name tag, then removes the OpenTofu workspace. It never matches resources by name prefix, so one deployment's `stack_name_prefix` must not be a prefix of another deployment's on the same cloud project. A destroy blocked by a state lock fails the stage, and the lock is released only when no allocation job of the unit is alive any more.
* The networking stage fails when a NetBird object or the jump host home of the unit cannot be removed, so the unit stays and the cleanup is retried.
* A name that starts with `<stack_name_prefix>-` but carries no pool and unit id is logged as an error and left alone.
* A failed cleanup starts itself again up to `cleanup_retry_limit` times (3 by default), `cleanup_retry_delay` seconds apart (60 by default), then stays failed. Starting a cleanup by hand resets the count. A delay above 0 uses RQ's scheduler, so the worker of the default queue has to run with `--with-scheduler`; with a delay of 0 the retry is queued at once.

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
| `sandbox_instance_app/tests/test_jump_proxy_cleanup.py` | Unit | SSH key removal on the jump proxy, its failure raised |
| `sandbox_instance_app/tests/test_allocation_units.py` | Unit | Allocation unit API views, trainee self-allocation and access |
| `sandbox_instance_app/tests/test_permissions.py` | Unit | Trainee self-service permissions |
| `sandbox_instance_app/tests/test_allocation_unit_state_filters.py` | Unit | Allocation unit state expressions |
| `sandbox_instance_app/tests/test_job_liveness.py` | Unit | Liveness of the RQ job of a stage |
| `sandbox_instance_app/tests/test_stage_cancellation.py` | Unit | Cancellation of stages and requests, Cancel endpoints |
| `sandbox_instance_app/tests/test_allocation_cancellation.py` | Unit | Cancel all allocations, cancel of a queued allocation |
| `sandbox_instance_app/tests/test_stack_removal.py` | Unit | Removal of every leftover of one stack |
| `sandbox_instance_app/tests/test_netbird_orphans.py` | Unit | NetBird listing and deletion by stack name |
| `sandbox_instance_app/tests/test_openapi_schema.py` | Unit | OpenAPI component names, Cancel all allocations schema |
| `sandbox_instance_app/tests/test_cleanup_request_creation.py` | Unit | Cleanup request created before the sandbox is deleted |
| `sandbox_instance_app/tests/test_stage_detail.py` | Unit | Stage detail endpoints, 404 for a request without its stage |
| `sandbox_instance_app/tests/test_trainee_cleanup.py` | Unit | Cleanup of trainee sandboxes and its command |
| `sandbox_instance_app/tests/test_lost_jobs.py` | Unit | Detection and recovery of lost jobs, and its command |
| `sandbox_instance_app/tests/test_stack_leftovers.py` | Unit | Removal of stacks no unit owns, and its command |
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

### Lost jobs

A sandbox build or cleanup stops moving when its RQ job is lost: Redis is flushed or restarted, or a worker dies and RQ does not collect its job. `python manage.py observe_lost_jobs` finds such work and hands it to the normal failure path: the stage is marked failed and, for an allocation, its cleanup is queued; a failed cleanup is retried as described in [Cleanup](#cleanup). It handles

* the first unfinished stage of an allocation or cleanup request whose job is missing from Redis, or is dead (failed, stopped, or started on a worker that no longer exists) for longer than `lost_job_observer.grace_seconds` (900 by default; RQ collects the job of a dead worker within ~11.5 minutes, and the callback of a failed allocation job handles that case first),
* a user Ansible stage waiting for a NetBird provisioning job that died,
* an allocation or cleanup request without stages whose stage-creating job is gone: the unit of such an allocation is removed like a cancelled queued one, the stages of such a cleanup are created failed and the cleanup is retried,
* an allocation that failed with a lost or abandoned job and got no cleanup.

Stages with a pending stop request are left to the Cancel that requested it. `--dry-run` only logs what would be recovered. The command does nothing while `lost_job_observer.enabled` is false and exits non-zero when a recovery failed. Run it every few minutes, for example as a Kubernetes CronJob with the service's image and configuration; the placeholders below are to be replaced:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: sandbox-service-observe-lost-jobs
spec:
  schedule: "*/5 * * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 0
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: observe-lost-jobs
              image: ghcr.io/cyberrangecz/backend-sandbox-platform/sandbox-service:v<version>
              command: ["python", "manage.py", "observe_lost_jobs"]
              volumeMounts:
                - name: service-configuration
                  mountPath: <path the service reads its configuration from>
          volumes:
            - name: service-configuration
              secret:
                secretName: <the secret holding the service configuration>
```

### Leftover stacks

`python manage.py remove_leftover_stacks` is a command to run by hand, once. It lists the stacks of the deployment's `stack_name_prefix` that cloud resources, OpenTofu workspaces, NetBird objects or jump host homes still hold, and removes every one that no allocation unit owns, the way the cleanup does. The owner is checked again right before each removal. `--dry-run` lists the stacks and where each was found, and removes nothing. It exits non-zero when a listing or a removal failed.
