# AGENTS.md: automated-problem-generation-lib

Workspace-wide rules (uv, tox, checks, branching) are in [../../AGENTS.md](../../AGENTS.md). This file covers only this package.

**Distribution:** `crczp-automated-problem-generation-lib` · **Import:** `generator` · **Used by:** sandbox-service (`sandbox_definition_app/views.py`)

Generates random values for the variables a training definition declares (text, names, ports, IP addresses, passwords). sandbox-service seeds it with the user's ID, so each user gets their own values. It is independent of the other libraries.

## Module map

* `var_generator.py`: `generate(variable_list, seed)`, the entry point and the only export, which fills each variable with a seeded random value; the `get_random_*` helpers do one type each.
* `var_parser.py`: `parser_var_file()` / `get_variables()`, which turn a variables YAML file into `Variable` objects.
* `var_object.py`: `Variable`, one declared variable and its constraints.
* `generator/names.txt`, `generator/text.txt`: the word lists the text and name generators draw from.

## Package notes

* The package is a top-level `generator`, not under the `crczp.` namespace like the other libraries, so pylint is run as `pylint generator tests`.
* Ruff ignores `S311` here: `random` is used for non-cryptographic values only.
* The tests read `tests/variables.yml` by a relative path, so run them from this directory (as `tox` does).
