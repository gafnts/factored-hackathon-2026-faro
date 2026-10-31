"""
Data quality profile of the pinned snapshot: what the bronze contracts and quality checks will have to handle.
"""

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import duckdb

from faro import clock
from faro.analysis import attribution
from faro.analysis.attribution import Attribution, Cutoff
from faro.analysis.catalog import Column, Table
from faro.analysis.source import (
    AnalysisError,
    connect,
    headers,
    one,
    partition_date,
    quote,
    table_keys,
)
from faro.dataset.lock import Lock

PLACEHOLDERS = ("", "null", "none", "nan", "n/a", "na")
RECENT_DAYS = 14
TRAILING_DAYS = 28
LISTED_VALUES = 20
LAG_BUCKETS = (
    "dated after its process date",
    "same day",
    "1 day late",
    "2 to 7 days late",
    "8 to 30 days late",
    "over 30 days late",
)
COUNTED = {"call_transcripts": ("detected_language",)}

_CASTS = {
    "INTEGER": "integer",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "BOOLEAN": "boolean",
    "TIME": "time",
}

Log = Callable[[str], None]


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    type: str
    not_null: bool
    nulls: int
    placeholders: int
    padded: int
    invalid: int
    # None when the dictionary documents no closed set of values.
    outside: int | None
    outside_values: tuple[str, ...]
    spellings: tuple[str, ...]
    low: str | None
    high: str | None
    longest: int | None


@dataclass(frozen=True)
class Duplicates:
    exact: int
    redelivered: int
    conflicting: int
    repeated_keys: int


@dataclass(frozen=True)
class Arrival:
    first_partition: date
    last_partition: date
    missing_partitions: int
    misfiled: int
    undated: int
    first_event: date | None
    last_event: date | None
    lags: dict[str, int]
    # Latest time of day among events dated the day after their process date: a processing cutoff shows up here.
    next_day_until: str | None
    lag_p99: int | None
    last_complete_day: date | None
    recent: tuple[tuple[date, float | None], ...]


@dataclass(frozen=True)
class TableProfile:
    name: str
    files: int
    header_variants: int
    unexpected_columns: tuple[str, ...]
    missing_columns: tuple[str, ...]
    rows: int
    duplicates: Duplicates
    repeated_unique: dict[str, int]
    columns: tuple[ColumnProfile, ...]
    orphans: dict[str, int]
    arrival: Arrival | None
    value_counts: dict[str, dict[str, int]]


@dataclass(frozen=True)
class Profile:
    snapshot_id: str
    duckdb_version: str
    tables: tuple[TableProfile, ...]
    # Only for catalogs that hold the tables these checks read.
    attribution: Attribution | None
    cutoffs: tuple[Cutoff, ...]

    @property
    def business_date(self) -> date | None:
        return clock.business_date(
            t.arrival.last_complete_day for t in self.tables if t.arrival
        )

    @property
    def as_of(self) -> datetime | None:
        business_date = self.business_date
        if business_date is None:
            return None
        return clock.as_of(
            business_date,
            (
                time.fromisoformat(t.arrival.next_day_until)
                if t.arrival.next_day_until
                else None
                for t in self.tables
                if t.arrival
            ),
        )


def profile(
    lock: Lock, root: Path, tables: Sequence[Table], log: Log = print
) -> Profile:
    keys = {t.name: table_keys(lock, t) for t in tables}
    empty = [name for name, found in keys.items() if not found]
    if empty:
        raise AnalysisError(f"the lock has no files for {', '.join(empty)}")
    catalog = {t.name: t for t in tables}
    con = connect(root, tables, keys)
    try:
        results = []
        for t in tables:
            log(f"Profiling {t.name} ({len(keys[t.name]):,} files)")
            results.append(_profile_table(con, t, catalog, root, keys[t.name]))
        log("Checking contact attribution and processing-day cutoffs")
        traced = (
            attribution.contact_attribution(con)
            if catalog.keys() >= attribution.TABLES
            else None
        )
        ends = attribution.cutoffs(con, tables) if "customers" in catalog else ()
    finally:
        con.close()
    return Profile(lock.snapshot_id, duckdb.__version__, tuple(results), traced, ends)


def _profile_table(
    con: duckdb.DuckDBPyConnection,
    t: Table,
    catalog: Mapping[str, Table],
    root: Path,
    keys: Sequence[str],
) -> TableProfile:
    variants = headers(root, keys)
    found = {name for header in variants for name in header}
    expected = {c.name for c in t.columns}
    present = tuple(c for c in t.columns if c.name in found)
    rows, columns = _columns(con, t, present)
    return TableProfile(
        name=t.name,
        files=len(keys),
        header_variants=len(variants),
        unexpected_columns=tuple(sorted(found - expected)),
        missing_columns=tuple(sorted(expected - found)),
        rows=rows,
        duplicates=_duplicates(con, t),
        repeated_unique={
            u: one(
                con,
                f"select count(*) from (select {quote(u)} from {_raw(t)} "
                f"where {quote(u)} is not null group by 1 "
                f"having count(distinct {_key(t)}) > 1)",
            )[0]
            for u in t.unique
        },
        columns=columns,
        orphans={
            c.name: _orphans(con, t, c, catalog[c.references])
            for c in present
            if c.references
        },
        arrival=_arrival(con, t, catalog, keys) if t.daily else None,
        value_counts={
            name: _value_counts(con, t, name) for name in COUNTED.get(t.name, ())
        },
    )


def _raw(t: Table) -> str:
    return quote("raw_" + t.name)


def _key(t: Table) -> str:
    if len(t.key) == 1:
        return quote(t.key[0])
    return "row(" + ", ".join(quote(k) for k in t.key) + ")"


def _invalid(c: Column) -> str:
    q = quote(c.name)
    if c.base_type == "VARCHAR":
        return f"length({q}) > {c.type.removeprefix('VARCHAR(').removesuffix(')')}"
    if c.base_type == "TEXT":
        return "false"
    target = c.type.lower() if c.base_type == "DECIMAL" else _CASTS[c.base_type]
    return f"try_cast({q} as {target}) is null"


def _typed(c: Column) -> str | None:
    q = quote(c.name)
    if c.base_type == "INTEGER":
        return f"try_cast({q} as bigint)"
    if c.base_type == "DECIMAL":
        return f"try_cast({q} as {c.type.lower()})"
    if c.base_type in _CASTS and c.base_type != "BOOLEAN":
        return f"try_cast({q} as {_CASTS[c.base_type]})"
    return None


def _column_aggregates(c: Column) -> list[str]:
    q = quote(c.name)
    placeholders = ", ".join(f"'{p}'" for p in PLACEHOLDERS)
    documented = ", ".join("'" + v.replace("'", "''") + "'" for v in c.values)
    outside = f"{q} is not null and {q} not in ({documented})"
    typed = _typed(c)
    return [
        f"count(*) filter (where {q} is null)",
        f"count(*) filter (where lower(trim({q})) in ({placeholders}))",
        f"count(*) filter (where {q} <> trim({q}))",
        f"count(*) filter (where {q} is not null and {_invalid(c)})",
        f"count(*) filter (where {outside})" if c.values else "null",
        f"list_slice(list_sort(list(distinct {q}) filter (where {outside})), 1, {LISTED_VALUES})"
        if c.values
        else "[]",
        f"list_slice(list_sort(list(distinct {q}) filter (where {q} is not null)), 1, {LISTED_VALUES})"
        if c.base_type == "BOOLEAN"
        else "[]",
        f"min({typed})::varchar" if typed else "null",
        f"max({typed})::varchar" if typed else "null",
        f"max(length({q}))" if c.base_type in ("VARCHAR", "TEXT") else "null",
    ]


def _columns(
    con: duckdb.DuckDBPyConnection, t: Table, columns: Sequence[Column]
) -> tuple[int, tuple[ColumnProfile, ...]]:
    per_column = [_column_aggregates(c) for c in columns]
    width = len(per_column[0]) if per_column else 0
    select = ", ".join(["count(*)", *(a for aggs in per_column for a in aggs)])
    row = one(con, f"select {select} from {_raw(t)}")
    profiles = []
    for i, c in enumerate(columns):
        (
            nulls,
            placeholders,
            padded,
            invalid,
            outside,
            outside_values,
            spellings,
            low,
            high,
            longest,
        ) = row[1 + i * width : 1 + (i + 1) * width]
        profiles.append(
            ColumnProfile(
                name=c.name,
                type=c.type,
                not_null=c.not_null,
                nulls=nulls,
                placeholders=placeholders,
                padded=padded,
                invalid=invalid,
                outside=outside,
                outside_values=tuple(outside_values or ()),
                spellings=tuple(spellings or ()),
                low=low,
                high=high,
                longest=longest,
            )
        )
    return row[0], tuple(profiles)


def _duplicates(con: duckdb.DuckDBPyConnection, t: Table) -> Duplicates:
    raw = _raw(t)
    key = _key(t)
    keyed = " and ".join(f"{quote(k)} is not null" for k in t.key)
    ignored = "filename, process_date" if t.daily else "filename"
    rows, distinct_rows, redelivered_rows, versions, keys, repeated = one(
        con,
        f"""
        select
          (select count(*) from {raw}),
          (select count(*) from (select distinct * exclude (filename) from {raw})),
          (select count(*) from (select distinct * exclude ({ignored}) from {raw})),
          (select count(*) from (select distinct * exclude ({ignored}) from {raw} where {keyed})),
          (select count(distinct {key}) from {raw} where {keyed}),
          (select count(*) from (select {key} as k from {raw} where {keyed} group by k having count(*) > 1))
        """,
    )
    return Duplicates(
        exact=rows - distinct_rows,
        redelivered=distinct_rows - redelivered_rows,
        conflicting=versions - keys,
        repeated_keys=repeated,
    )


def _orphans(con: duckdb.DuckDBPyConnection, t: Table, c: Column, parent: Table) -> int:
    if len(parent.key) != 1:
        raise AnalysisError(f"{t.name}.{c.name} references a composite key")
    q = quote(c.name)
    count: int = one(
        con,
        f"select count(*) from {_raw(t)} c where c.{q} is not null and not exists "
        f"(select 1 from {_raw(parent)} p where p.{quote(parent.key[0])} = c.{q})",
    )[0]
    return count


def _value_counts(
    con: duckdb.DuckDBPyConnection, t: Table, column: str
) -> dict[str, int]:
    q = quote(column)
    rows = con.execute(
        f"select coalesce({q}, '(null)'), count(*) from {_raw(t)} group by 1 order by 2 desc, 1"
    ).fetchall()
    return {value: n for value, n in rows}


def _event_day(t: Table, catalog: Mapping[str, Table]) -> tuple[str, str]:
    if t.event_date is None:
        raise AnalysisError(f"{t.name} has no event date")
    raw = _raw(t)
    column = quote(t.event_date.column)
    if t.event_date.via is None:
        return f"try_cast({column} as timestamp)", raw
    via = t.column(t.event_date.via)
    if via.references is None:
        raise AnalysisError(f"{t.name}.{via.name} references no table")
    parent = catalog[via.references]
    joined = (
        f"{raw} t left join (select {quote(parent.key[0])} as ref, "
        f"min(try_cast({column} as timestamp)) as happened "
        f"from {_raw(parent)} group by 1) e on t.{quote(via.name)} = e.ref"
    )
    return "e.happened", joined


def _bucket(lag: int) -> str:
    if lag < 0:
        return LAG_BUCKETS[0]
    if lag <= 1:
        return LAG_BUCKETS[1 + lag]
    if lag <= 7:
        return LAG_BUCKETS[3]
    if lag <= 30:
        return LAG_BUCKETS[4]
    return LAG_BUCKETS[5]


def _arrival(
    con: duckdb.DuckDBPyConnection,
    t: Table,
    catalog: Mapping[str, Table],
    keys: Sequence[str],
) -> Arrival:
    partitions = sorted({partition_date(k) for k in keys})
    first, last = partitions[0], partitions[-1]
    expected_days = (last - first).days + 1
    happened, source = _event_day(t, catalog)
    table = quote("arrival_" + t.name)
    con.execute(
        f"create temp table {table} as select "
        r"strptime(regexp_extract(filename, '_(\d{8})\.csv$', 1), '%Y%m%d')::date as filed, "
        f"try_cast(process_date as date) as processed, {happened} as happened_at, "
        f"happened_at::date as happened from {source}"
    )
    try:
        misfiled, undated, first_event, last_event, next_day_until = one(
            con,
            f"select count(*) filter (where processed is distinct from filed), "
            f"count(*) filter (where happened is null), min(happened), max(happened), "
            f"max(happened_at::time) filter (where happened - processed = 1)::varchar "
            f"from {table}",
        )
        settled_before = clock.settled_before(last)
        lags: dict[str, int] = dict.fromkeys(LAG_BUCKETS, 0)
        settled: dict[int, int] = {}
        for lag, n_settled, n in con.execute(
            f"select processed - happened, count(*) filter (where happened <= ?), count(*) "
            f"from {table} where processed is not null and happened is not null group by 1",
            [settled_before],
        ).fetchall():
            lags[_bucket(lag)] += n
            settled[lag] = n_settled
        start = last - timedelta(days=RECENT_DAYS + TRAILING_DAYS - 1)
        daily = dict(
            con.execute(
                f"select happened, count(*) from {table} "
                "where happened between ? and ? group by 1",
                [start, last],
            ).fetchall()
        )
    finally:
        con.execute(f"drop table {table}")
    p99 = clock.lag_p99(settled)
    return Arrival(
        first_partition=first,
        last_partition=last,
        missing_partitions=expected_days - len(partitions),
        misfiled=misfiled,
        undated=undated,
        first_event=first_event,
        last_event=last_event,
        lags=lags,
        next_day_until=next_day_until,
        lag_p99=p99,
        last_complete_day=clock.last_complete_day(last, p99),
        recent=_recent(daily, last),
    )


def _recent(
    daily: Mapping[date, int], last: date
) -> tuple[tuple[date, float | None], ...]:
    recent = []
    for back in range(RECENT_DAYS - 1, -1, -1):
        day = last - timedelta(days=back)
        trailing = statistics.median(
            daily.get(day - timedelta(days=d), 0) for d in range(1, TRAILING_DAYS + 1)
        )
        recent.append((day, daily.get(day, 0) / trailing if trailing else None))
    return tuple(recent)
