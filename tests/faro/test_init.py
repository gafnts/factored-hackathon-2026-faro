"""
Smoke test that the package is importable from the project environment.
"""

from importlib.metadata import version

import faro


def test_version_matches_distribution_metadata() -> None:
    assert faro.__version__ == version("faro")
