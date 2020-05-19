## Usage example

```toml
[[source]]
name = "pypi"
url = "https://pypi.org/simple"
verify_ssl = true

[[source]]
url = "https://kypo-storage.ics.muni.cz:8443/repository/pypi-group/simple"
name = "kypo"
verify_ssl = true

[packages]
kypo-topology-definition = { index = "kypo", version = ">=0.2.5" }

[requires]
python_version = "3.6"
```