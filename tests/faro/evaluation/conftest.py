"""
The evaluation's bank, built once per test session by the real pipeline: tests that read it run in one xdist group, so
one worker builds it.
"""

import pytest

from . import bank as bank_module


@pytest.fixture(scope="session")
def bank(tmp_path_factory: pytest.TempPathFactory) -> bank_module.Bank:
    return bank_module.install(tmp_path_factory.mktemp("bank"))
