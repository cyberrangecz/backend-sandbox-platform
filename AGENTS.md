# AGENTS.md

This is a `uv` workspace containing `sandbox-service` and the 6 libraries it depends on (`libs/*`). This file holds the rules for the whole workspace. Each package's own `AGENTS.md` adds only what is specific to that package (what it does, its module map, its deviations): read it before working there.

## Workspace conventions

* Dependency management is `uv`, resolved once for the whole workspace from the root `pyproject.toml` / `uv.lock`. Do not create a per-package lockfile or `.venv`.
* Internal dependencies between packages resolve locally via `[tool.uv.sources] ... = { workspace = true }` in each consumer's `pyproject.toml`: editing a library is immediately visible to `sandbox-service`, no version bump or publish step needed.
* Run `uv sync` from the repo root, not from inside a package.
* Task orchestration is per-package `tox` (`pylint`, `pytest`), run from within the package directory, e.g. `cd sandbox-service && tox`. Every env uses `runner = uv-venv-lock-runner`, so envs are built by `uv sync --locked` against the root `uv.lock`. That runner ignores `deps =`: declare tooling in the package's `[dependency-groups]` (`test`, `lint`, and `security` in sandbox-service) and reference it from `tox.ini` via `dependency_groups` / `only_groups`. After changing a group, re-run `uv lock`.
* Linting and type checking are workspace-level: `pre-commit run --all-files` from the repo root runs `ruff` (including its `S` security rules, which replaced bandit) and `ty`. Ruff's config is the root `[tool.ruff]`, which each package `extend`s, adding only its real deviations. `ty` reads one config from the invocation directory, so its settings live only in the root `[tool.ty]`: do not add per-package `[tool.ty]` sections.
* The dependency audit is workspace-wide and lives only in sandbox-service (`tox -e audit` there; see `sandbox-service/audit.sh`): sandbox-service depends on every library, so its locked set covers theirs. In CI it fails only on vulnerabilities a change introduces; inherited ones are fixed in their own PR to master. Accepted vulnerabilities go in `sandbox-service/audit-ignore.txt`, with a reason and a review date.
* Versions: each package keeps its own `version`; there is no monorepo version. Only sandbox-service is tagged, and its `version` must be above the latest git tag: the `check-version-bump` pre-commit hook enforces this on every commit and CI's `control_tag` on every PR, so bump it once per PR.
* The sandbox-service image builds from the repository root, not from `sandbox-service/`, because the workspace needs `libs/` in the build context: `docker build -f sandbox-service/Dockerfile .`
* Secrets: a `gitleaks` pre-commit hook blocks commits with secrets in them (run `pre-commit install` once), and CI runs gitleaks and TruffleHog over every PR's commits. Allowlist only test material that grants access to nothing, in `.gitleaks.toml`.

## Agent rules

* Run tools only through `uv run` or `tox`, never with system Python, a hand-made virtual environment, or globally installed packages.
* `master` is protected: work on a feature branch, merged only after all checks pass. Never push to `master`.
* Do not bypass or disable quality checks, add dependencies without justification, change tooling configuration unless asked, or commit generated or compiled artifacts.
* If requirements are unclear, prefer the conservative change and ask; do not assume undocumented behaviour.

## Running checks

Two bars apply; do not conflate them.

* **While iterating:** run only what your change touches: `ruff check`, `ruff format`, `ty check`, and the affected tests, either `tox -e pytest -- path/to/test_file.py` or `uv run --group test pytest path/to/test_file.py` from the package directory.
* **Before a change is complete:** `tox` in every package you changed, plus `pre-commit run --all-files` from the root. Together these are what CI runs.

There is no workspace-wide test run from the root: test tooling lives in each package's `test` group, which `uv sync` does not install, and each package's pytest configuration applies only from its own directory.

## Code rules

* Ruff is the only formatter and the authoritative linter; no manual formatting outside it.
* New and modified code is type-annotated for `ty`; avoid `Any` unless necessary.
* Pylint must score at least 9.5 in every package. Do not disable a warning without a reason.
* Security lint (ruff `S`): no `eval`/`exec` on untrusted input, no hard-coded secrets, no insecure crypto patterns. Suppress a finding only on its own line, with `# noqa: Sxxx` and the reason.
* New features need tests and bug fixes need a regression test; tests are deterministic and isolated.

## Package index

Dependency order, lowest first; each package's `AGENTS.md` has its module map.

* [libs/topology-definition](libs/topology-definition/AGENTS.md): the topology YAML schema, as pydantic models
* [libs/python-commons](libs/python-commons/AGENTS.md): cloud-agnostic layer: the driver interface, topology instances, stack naming
* [libs/openstack-lib](libs/openstack-lib/AGENTS.md), [libs/aws-lib](libs/aws-lib/AGENTS.md): the cloud drivers
* [libs/terraform-client](libs/terraform-client/AGENTS.md): deploys topologies through OpenTofu with either driver
* [libs/automated-problem-generation-lib](libs/automated-problem-generation-lib/AGENTS.md): random values for training variables (independent of the rest)
* [sandbox-service](sandbox-service/AGENTS.md): the Django REST service that uses all of the above
