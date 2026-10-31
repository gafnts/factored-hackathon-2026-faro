"""
The fixture's base, built once per test session: tests that read it run in one xdist group, so one worker builds it.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

from faro.dataset.lock import Lock
from faro.dataset.snapshot import snapshot_dir
from faro.pipeline import build, runner, version

from . import fixture


@dataclass(frozen=True)
class Built:
    lock: Lock
    lock_path: Path
    data_dir: Path
    space: runner.Workspace
    results: list[runner.Result]

    @property
    def root(self) -> Path:
        return snapshot_dir(self.data_dir, self.lock.snapshot_id)

    @property
    def stamp(self) -> dict[str, str]:
        return {
            "snapshot": self.lock.snapshot_id,
            "pipeline_version": version.pipeline_version(),
        }


def build_version(name: str, into: Path) -> Built:
    lock_path, data_dir, lock = fixture.install(name, into)
    space = runner.workspace(data_dir, lock.snapshot_id)
    results = build.build(lock, snapshot_dir(data_dir, lock.snapshot_id), space)
    return Built(lock, lock_path, data_dir, space, results)


@pytest.fixture(scope="session")
def base(tmp_path_factory: pytest.TempPathFactory) -> Built:
    return build_version("base", tmp_path_factory.mktemp("base"))
