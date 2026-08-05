# AGENTS.md

This is a `uv` workspace containing `sandbox-service` and its 6 library dependencies (`libs/*`). Each package keeps its own `AGENTS.md`, `pyproject.toml`, `tox.ini`, and test suite — read the package's own `AGENTS.md` before working in it. This file only covers workspace-wide conventions.

## Workspace conventions

* Dependency management is `uv`, resolved once for the whole workspace from the root `pyproject.toml` / `uv.lock` — do not create a per-package lockfile.
* Internal dependencies between packages resolve locally via `[tool.uv.sources] ... = { workspace = true }` in each consumer's `pyproject.toml` — editing a library is immediately visible to `sandbox-service`, no version bump or publish step needed.
* Each package keeps its own independent `version` in its own `pyproject.toml`. There is no unified monorepo version.
* Run `uv sync` from the repo root, not from inside a package.
* Task orchestration is still per-package `tox` (`pre-commit`, `pylint`, `bandit`, `audit`, `pytest`) — run it from within the package directory, e.g. `cd sandbox-service && tox`.
* `master` is the protected, stable branch across the whole repo. All changes go through feature branches.

Agents must not introduce a per-package `.venv` or bypass the workspace-level `uv.lock`.

## Package index

* [sandbox-service/AGENTS.md](sandbox-service/AGENTS.md) — Django REST application
* [libs/topology-definition/AGENTS.md](libs/topology-definition/AGENTS.md)
* [libs/python-commons/AGENTS.md](libs/python-commons/AGENTS.md)
* [libs/openstack-lib/AGENTS.md](libs/openstack-lib/AGENTS.md)
* [libs/aws-lib/AGENTS.md](libs/aws-lib/AGENTS.md)
* [libs/terraform-client/AGENTS.md](libs/terraform-client/AGENTS.md)
* [libs/automated-problem-generation-lib/AGENTS.md](libs/automated-problem-generation-lib/AGENTS.md)
