# AGENTS.md: sandbox-service

Workspace-wide rules (uv, tox, checks, branching, code rules) are in [../AGENTS.md](../AGENTS.md). This file covers only this package.

**Distribution:** `crczp-sandbox-service` · **Import:** `crczp.sandbox_*` · **Depends on:** every library in `libs/`

The Django REST Framework service that simplifies manipulation of the OpenStack cloud platform for the CyberRangeCZ Platform. It manages sandbox definitions, pools of sandboxes, sandbox instances, and Ansible playbook execution on sandbox machines. The REST API is documented via OpenAPI (drf-spectacular). It is the only package that is versioned, tagged and shipped, as a Docker image.

## Package structure

Shared code lives in `crczp/sandbox_common_lib` (config, exceptions, permissions, construction of the cloud client) and `crczp/sandbox_uag` (auth: OIDC/JWT, permissions).

Each of `sandbox_ansible_app`, `sandbox_cloud_app`, `sandbox_definition_app`, and `sandbox_instance_app` is a self-contained Django app with its own `lib/`, `migrations/`, and `tests/`. `sandbox_service_project` holds project-wide settings, root URLs, and WSGI.

`tofu/` holds the OpenTofu provider configuration the image mirrors at build time; it is not part of the Django app and is not covered by `tox`/`pytest`.

Keep this layout; do not introduce an alternative one without explicit approval.

## Setup

* A root `uv sync` also installs this package's `dev` group (type stubs for `ty`). The `test`, `lint` and `security` groups are for tox.
* `manage.py` needs a `config.yml`: run `cp config.yml-template config.yml` before `python manage.py check` or any other management command outside tox. `config.yml` is git-ignored, because it is where local credentials go.
* Build the image from the repository root: `docker build -f sandbox-service/Dockerfile .`

## Checks

`tox` here runs three environments:

* `pylint` lints `crczp`, which already contains every app's `tests/` package, against the 9.5 floor.
* `audit` is the dependency audit for the whole workspace (`audit.sh`). A plain `tox -e audit` fails on every known vulnerability, including ones already on `master`. CI fails a branch only on the ones it introduces, and so does `BASE=$(git merge-base origin/master HEAD) tox -e audit`. Inherited ones are fixed in their own PR to `master`, not in an unrelated change.
* `pytest` runs the suite in two passes, `-m "not integration"` and then `-m integration`, and then `python manage.py check`. Marking a test `integration` moves it to the second pass; it does not exclude it from `tox`.

## Testing notes

* Test settings: `crczp.sandbox_service_project.tests.settings`, set in both `[tool.pytest.ini_options]` and `tox.ini`.
* `@pytest.mark.integration` marks tests that run the RQ job pipeline. The root `conftest.py` gives each of them a shared fakeredis server and the `default`, `openstack` and `ansible` queues, so no real Redis is needed.
* Test paths are each app's `crczp/<app>/tests` (`testpaths` in `pyproject.toml`).
* `ty` has no plugin system, so the Django ORM's dynamic attributes (reverse relations, multi-table-inheritance accessors) are not inferred: declare them on the model.
