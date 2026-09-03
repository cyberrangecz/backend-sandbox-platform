"""
Import smoke test.

`crczp.cloud_commons` re-exports every submodule, so importing it exercises the whole
package and fails if any runtime dependency is undeclared. That is not hypothetical: this
package imported yaml, netaddr, typing_extensions and yamlize while declaring only
crczp-topology-definition, and passed CI purely because its consumers happened to pull
them in.
"""

import crczp.cloud_commons as cloud_commons


def test_public_api_is_importable() -> None:
    """Every name in __all__ resolves, so no runtime dependency is missing."""
    assert cloud_commons.__all__
    for name in cloud_commons.__all__:
        assert getattr(cloud_commons, name, None) is not None, f'{name} failed to import'


def test_cloud_client_base_is_abstract() -> None:
    """The provider contract the two cloud drivers implement."""
    assert cloud_commons.CrczpCloudClientBase.__abstractmethods__
