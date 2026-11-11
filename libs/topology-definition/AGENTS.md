# AGENTS.md: topology-definition

Workspace-wide rules (uv, tox, checks, branching) are in [../../AGENTS.md](../../AGENTS.md). This file covers only this package.

**Distribution:** `crczp-topology-definition` · **Import:** `crczp.topology_definition` · **Used by:** python-commons, openstack-lib, aws-lib, sandbox-service

The schema of a sandbox topology file (hosts, routers, networks, groups, network forwarding) as pydantic models loaded from YAML, plus the validation of a loaded topology. It is the bottom of the dependency graph: a change here reaches every other package except automated-problem-generation-lib.

## Module map

* `models.py`: the schema. `TopologyDefinition` is the root model; `Host`, `Router`, `Network`, `NetworkMapping`, `DockerContainers`, `Volume` and the rest are its parts.
* `yaml_model.py`: `YamlModel`, the pydantic base that loads from YAML with ruamel's round-trip parser (YAML 1.2, merge keys), rejects unknown keys, and raises `YamlModelError` naming the place in the document. It loads the way the `yamlize` objects it replaced did, which is what keeps existing topology files loading.
* `validators.py`: `TopologyValidation`, the cross-field checks a single model cannot express (unique names, CIDR overlaps, mappings, role visibility), and `ROLE_NAME_REGEX`.
* `image_naming.py`, `utils.py`: image-name rewriting, and helpers for deprecated attributes and key conflicts.

## Package notes

* Ruff ignores `S311` here: `random` is used for non-cryptographic values only.
