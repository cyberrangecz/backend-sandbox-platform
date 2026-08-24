# AGENTS.md

This document defines how automated agents (CI systems, bots, or AI coding assistants)
should interact with this repository.

## Project Overview

For a high-level description, usage examples, and API overview, see the main project documentation:

[README.md](https://github.com/cyberrangecz/backend-automated-problem-generation-lib/blob/master/README.md)

**Repository:** `backend-automated-problem-generation-lib`

A Python library for automated problem generation with a strong focus on code quality,
security, and test coverage.

Core tooling:

* **Package Manager:** `uv`
* **Project configuration:** `pyproject.toml`
* **Task orchestration:** `tox`
* **Code quality:** `pre-commit`, `ruff`, `mypy`, `pylint`
* **Security:** `bandit`, dependency audit
* **Testing:** `pytest`

---

## Branching Model

* `master` is the protected, stable branch
* All changes must be made via **feature branches**
* Feature branches are merged into `master` only after all checks pass

Agents **must not** push directly to `master`.

---

## Project Structure — Key Directories

```
/
├── .github/                    # GitHub CI/CD configuration
│   └── workflows/              # CI workflows
├── generator/                  # Main Python package
│   └── *.py                    # Library modules
├── tests/                      # Pytest test suite
│   └── *.py                    # Test modules
├── .pre-commit-config.yaml     # Pre-commit hook definitions
├── pyproject.toml              # Project and tool configuration
├── tox.ini                     # Tox environments
├── uv.lock                     # Locked dependency versions
├── README.md
├── LICENSE
└── AGENTS.md                   # This file
```

* **`generator/`** contains all production library code
* **`tests/`** contains the complete automated test suite
* **`.github/workflows/`** defines CI behavior

Agents must respect this structure and must not introduce alternative layouts without
explicit approval.

---

## Environment Setup

Agents must use **`uv`** for dependency management.

```bash
uv sync
```

There are **no separate development dependencies**.

Python version must match the version specified in `pyproject.toml`.

---

## Virtual Environments and Isolation

Agents must not run tools using arbitrary or external virtual environments.

- All tooling must be executed via `tox` or `uv run`
- Agents must not invoke tools using:
  - system Python
  - manually created virtual environments
  - virtual environments not managed by `uv` or `tox`

Agents should rely on `uv` and `tox` for environment isolation and must not
invoke tools using globally installed Python packages.

---

## How Agents Must Run Code Quality Checks

Code quality checks must be executed in a reproducible, CI-equivalent environment.

### Authoritative Method

- The **only authoritative way** to run code quality checks is:

  ```bash
  tox
  ```

## Code Quality Requirements

All changes **must pass the full quality suite** before merging.

### Pre-commit

Run all hooks locally:

```bash
pre-commit run --all-files
```

Configured hooks include:

* `ruff` (linting)
* `ruff format` (formatting)
* `mypy` (static type checking)

Agents must ensure all hooks pass.

---

## Linting and Static Analysis

### Ruff

* Ruff is the authoritative linter and formatter
* Rules are defined in `pyproject.toml`
* Manual formatting outside Ruff is not allowed

### MyPy

* All new and modified code must be fully type-annotated
* Avoid `Any` unless absolutely necessary

### Pylint

* Code must satisfy configured Pylint rules
* Warnings should not be disabled without justification

---

## Security Checks

### Bandit

Bandit is used to detect common security issues.

Agents must not introduce:

* Unsafe `eval` or `exec` usage
* Hard-coded secrets
* Insecure cryptographic patterns

### Dependency Audit

Agents must not introduce dependencies with known vulnerabilities.

---

## Testing

### Pytest

All changes must include appropriate test coverage.

```bash
pytest
```

Guidelines:

* New features require new tests
* Bug fixes must include regression tests
* Tests must be deterministic and isolated

---

## Tox

`tox` is the authoritative CI entry point.

Agents should prefer:

```bash
tox
```

All tox environments must pass before merging.

---

## Coding Standards

* Follow PEP 8 and project conventions
* Prefer clarity over cleverness
* Keep functions small and focused
* Document public APIs with docstrings
* Avoid breaking changes unless explicitly requested

---

## What Agents Must Not Do

* Do not commit directly to `master`
* Do not bypass or disable quality checks
* Do not introduce new dependencies without justification
* Do not modify tooling configuration unless requested
* Do not commit generated or compiled artifacts

---

## Recommended Agent Workflow

1. Create a feature branch
2. Make minimal, focused changes
3. Run:

   ```bash
   tox
   ```

   This runs the full suite, including:

   * `pylint`
   * `bandit`
   * dependency audit
   * `pytest`
4. Ensure all checks pass

---

## Handling Uncertainty

If requirements are unclear:

* Prefer conservative changes
* Ask for clarification
* Do not assume undocumented behavior

---

This repository prioritizes **quality, security, and maintainability**.
Agents are expected to follow these rules strictly.
