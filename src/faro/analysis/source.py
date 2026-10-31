"""
Opens the pinned snapshot in data/ as DuckDB views, one per table, every value read as text.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from faro.analysis.catalog import Table
from faro.dataset.lock import Lock

_PARTITION = re.compile(r"_(\d{8})\.csv$")


class AnalysisError(Exception):
    pass


def table_keys(lock: Lock, table: Table) -> list[str]:
    if table.daily:
        return [f.key for f in lock.files if f.key.startswith(f"{table.name}/")]
    return [f.key for f in lock.files if f.key == f"{table.name}.csv"]


def partition_date(key: str) -> date:
    match = _PARTITION.search(key)
    if not match:
        raise AnalysisError(f"{key} doesn't end in a _YYYYMMDD.csv partition date")
    return datetime.strptime(match[1], "%Y%m%d").date()


def unlisted(lock: Lock, root: Path) -> list[str]:
    """
    Files under the snapshot's directory that the lock doesn't list; hidden ones, such as the .DS_Store a file browser
    leaves, aren't the snapshot's.
    """
    locked = {f.key for f in lock.files}
    found = (
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    )
    return sorted(key.as_posix() for key in found if key.as_posix() not in locked)


def check_local(lock: Lock, root: Path) -> None:
    missing = []
    resized = []
    for f in lock.files:
        path = root / f.key
        if not path.is_file():
            missing.append(f.key)
        elif path.stat().st_size != f.size:
            resized.append(f.key)
    extra = unlisted(lock, root)
    if missing or resized or extra:
        named = "".join(f"\n  not in the lock: {key}" for key in extra[:5])
        raise AnalysisError(
            f"{root} doesn't hold snapshot {lock.snapshot_id}: {len(missing)} files missing, "
            f"{len(resized)} with the wrong size, {len(extra)} not in the lock; run make data, "
            f"and remove any file the lock doesn't list{named}"
        )


def headers(root: Path, keys: Iterable[str]) -> Counter[tuple[str, ...]]:
    variants: Counter[tuple[str, ...]] = Counter()
    for key in keys:
        with (root / key).open(encoding="utf-8-sig") as fh:
            variants[tuple(fh.readline().rstrip("\r\n").split(","))] += 1
    return variants


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def connect(
    root: Path, tables: Sequence[Table], keys: Mapping[str, Sequence[str]]
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("set enable_progress_bar = false")
    for t in tables:
        files = ", ".join(_literal(str(root / key)) for key in keys[t.name])
        con.execute(
            f"create view {quote('raw_' + t.name)} as select * from read_csv([{files}], "
            "header = true, delim = ',', quote = '\"', escape = '\"', "
            "all_varchar = true, union_by_name = true, filename = true)"
        )
    return con


def one(
    con: duckdb.DuckDBPyConnection, sql: str, params: Sequence[object] | None = None
) -> tuple[Any, ...]:
    row = con.execute(sql, params).fetchone()
    if row is None:
        raise AnalysisError(f"no result from {sql}")
    return row
