"""
What the build checks before dbt reads a file (ADR-0006, Contracts and Quality checks): the snapshot's directory holds
exactly the lock's files, and each file's header is its contract's, since DuckDB's typed read accepts a renamed column
when the count matches. Each file's records are then counted by Python's csv module, apart from DuckDB's reader, and
loaded with the lock beside bronze, where its checks compare them with what DuckDB read. Every message names files,
never a row's values (SEC-03).
"""

import csv
import os
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import duckdb

from faro.analysis.source import check_local, table_keys
from faro.dataset.lock import Lock
from faro.pipeline.contracts import TableContract
from faro.pipeline.runner import PipelineError, Result

NAMED = 5
# Below this, starting the workers costs more than the count.
PARALLEL_FROM = 500


def _difference(expected: Sequence[str], found: Sequence[str]) -> str:
    added = [c for c in found if c not in expected]
    missing = [c for c in expected if c not in found]
    if not added and not missing:
        return "the contract's columns in another order"
    parts = [
        f"{label} {', '.join(names)}"
        for label, names in (("added", added), ("missing", missing))
        if names
    ]
    return "; ".join(parts)


def header_problems(
    lock: Lock, root: Path, contracts: Sequence[TableContract]
) -> list[str]:
    problems = []
    for contract in contracts:
        expected = [c.name for c in contract.columns]
        for key in table_keys(lock, contract.table):
            with (root / key).open(encoding="utf-8-sig", newline="") as fh:
                found = next(csv.reader(fh), [])
            if found != expected:
                problems.append(f"{key}: {_difference(expected, found)}")
    return problems


def check_files(lock: Lock, root: Path, contracts: Sequence[TableContract]) -> None:
    check_local(lock, root)
    problems = header_problems(lock, root, contracts)
    if problems:
        listed = "\n  ".join(problems[:NAMED])
        more = f"\n  and {len(problems) - NAMED} more" if len(problems) > NAMED else ""
        raise PipelineError(
            f"{len(problems)} files' headers aren't their contracts':\n  {listed}{more}"
        )


def count(path: Path) -> int:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return max(sum(1 for row in csv.reader(fh) if row) - 1, 0)


def count_records(
    root: Path, keys: Sequence[str], workers: int | None = None
) -> dict[str, int]:
    paths = [root / key for key in keys]
    if len(paths) < PARALLEL_FROM or workers == 1:
        return {key: count(path) for key, path in zip(keys, paths, strict=True)}
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        counted = pool.map(count, paths, chunksize=32)
        return dict(zip(keys, counted, strict=True))


def load(database: Path, lock: Lock, records: Mapping[str, int]) -> None:
    files = sorted(lock.files, key=lambda f: f.key)
    with duckdb.connect(str(database)) as con:
        con.execute("create schema checks")
        con.execute(
            "create table checks.lock_files "
            "(key varchar primary key, size bigint not null, sha256 varchar not null)"
        )
        con.execute(
            "insert into checks.lock_files select unnest($keys), unnest($sizes), unnest($hashes)",
            {
                "keys": [f.key for f in files],
                "sizes": [f.size for f in files],
                "hashes": [f.sha256 for f in files],
            },
        )
        con.execute(
            "create table checks.record_counts (key varchar primary key, records bigint not null)"
        )
        con.execute(
            "insert into checks.record_counts select unnest($keys), unnest($records)",
            {
                "keys": [f.key for f in files],
                "records": [records[f.key] for f in files],
            },
        )


def failing_files(database: Path, results: Sequence[Result]) -> dict[str, list[str]]:
    """
    The file keys behind each failing check that stores its failures, which are keys and counts, never values.
    """
    stored = [
        r
        for r in results
        if str(r["unique_id"]).startswith("test.")
        and r["status"] in ("fail", "warn")
        and r.get("relation_name")
    ]
    if not stored:
        return {}
    named: dict[str, list[str]] = {}
    with duckdb.connect(str(database), read_only=True) as con:
        for r in stored:
            rows = con.execute(
                f"select key from {r['relation_name']} order by key limit {NAMED}"
            ).fetchall()
            named[str(r["unique_id"])] = [str(key) for (key,) in rows]
    return named
