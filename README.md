# backend-sandbox-platform

Monorepo for the CyberRangeCZ sandbox-service application and its library dependencies, managed as a single `uv` workspace. Each package keeps its own version, tests, and tooling config — see its own README for details.

## Packages

- [sandbox-service](sandbox-service/README.md) — the Django REST application ([backend-sandbox-service](https://github.com/cyberrangecz/backend-sandbox-service))
- [libs/topology-definition](libs/topology-definition/README.md) ([backend-topology-definition](https://github.com/cyberrangecz/backend-topology-definition))
- [libs/python-commons](libs/python-commons/README.md) ([backend-python-commons](https://github.com/cyberrangecz/backend-python-commons))
- [libs/openstack-lib](libs/openstack-lib/README.md) ([backend-openstack-lib](https://github.com/cyberrangecz/backend-openstack-lib))
- [libs/aws-lib](libs/aws-lib/README.md) ([backend-aws-lib](https://github.com/cyberrangecz/backend-aws-lib))
- [libs/terraform-client](libs/terraform-client/README.md) ([backend-terraform-client](https://github.com/cyberrangecz/backend-terraform-client))
- [libs/automated-problem-generation-lib](libs/automated-problem-generation-lib/README.md) ([backend-automated-problem-generation-lib](https://github.com/cyberrangecz/backend-automated-problem-generation-lib))

## Dependency graph

```
topology-definition
      └── python-commons
                ├── openstack-lib
                └── aws-lib
                        └── terraform-client
automated-problem-generation-lib (standalone)

sandbox-service depends on all of the above
```

## Development

```bash
uv sync          # resolves the whole workspace, one lockfile
uv run pytest    # or: cd <package> && tox -e pytest
```

Packages depend on each other via `[tool.uv.sources] ... = { workspace = true }`, so editing a library is immediately visible to `sandbox-service` — no version bump or publish step required.
