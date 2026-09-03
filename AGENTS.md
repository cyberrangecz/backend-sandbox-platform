# AGENTS.md

This is a `uv` workspace containing `sandbox-service` and its 6 library dependencies (`libs/*`). Each package keeps its own `AGENTS.md`, `pyproject.toml`, `tox.ini`, and test suite — read the package's own `AGENTS.md` before working in it. This file only covers workspace-wide conventions.

## Workspace conventions

* Dependency management is `uv`, resolved once for the whole workspace from the root `pyproject.toml` / `uv.lock` — do not create a per-package lockfile.
* Internal dependencies between packages resolve locally via `[tool.uv.sources] ... = { workspace = true }` in each consumer's `pyproject.toml` — editing a library is immediately visible to `sandbox-service`, no version bump or publish step needed.
* Each package keeps its own independent `version` in its own `pyproject.toml`. There is no unified monorepo version.
* Run `uv sync` from the repo root, not from inside a package.
* Task orchestration is still per-package `tox` (`pylint`, `bandit`, `audit`, `pytest`) — run it from within the package directory, e.g. `cd sandbox-service && tox`. Every env uses `runner = uv-venv-lock-runner`, so envs are built by `uv sync --locked` against the root `uv.lock`. That runner ignores `deps =`: declare tooling in the package's `[dependency-groups]` (`test`, `lint`, `security`) and reference it from `tox.ini` via `dependency_groups` / `only_groups`. After changing a group, re-run `uv lock`.
* Linting and type checking are workspace-level, not per-package: `pre-commit run --all-files` from the repo root runs `ruff` and `ty`. `ty` reads one config from the invocation directory rather than the nearest `pyproject.toml` per file, so its settings live in the root `pyproject.toml` under `[tool.ty]` — do not add per-package `[tool.ty]` sections.
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
