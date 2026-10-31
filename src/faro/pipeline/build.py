"""
One full build of the pipeline on a snapshot (ADR-0006, decision 2): the checks before any file is read, a fresh DuckDB
file with the lock and the record counts, then dbt's build of every layer and its checks.
"""

from pathlib import Path

from faro.dataset.lock import Lock
from faro.pipeline import checks, contracts, runner, version


def variables(lock: Lock, root: Path) -> dict[str, str]:
    return {
        "snapshot_root": str(root.resolve()),
        "snapshot_id": lock.snapshot_id,
        "pipeline_version": version.pipeline_version(),
    }


def build(
    lock: Lock, root: Path, space: runner.Workspace, workers: int | None = None
) -> list[runner.Result]:
    built = contracts.build()
    checks.check_files(lock, root, built)
    records = checks.count_records(root, [f.key for f in lock.files], workers)
    space.reset()
    checks.load(space.database, lock, records)
    results = runner.dbt(["build"], space, variables(lock, root))
    if not results:
        raise runner.PipelineError(
            f"dbt build recorded no results; its messages are in {space.logs}"
        )
    return results
