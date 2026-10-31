"""
How much traffic the development customers generate, by day, weekday, and hour, for the capacity projection.
Held-out customers are set aside by ADR-0003's rule before anything is read.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb

from faro.analysis.candidates import CARDS, sql_list
from faro.analysis.cards import (
    YEAR,
    Breakdown,
    Group,
    breakdown,
    define_days,
    split,
)
from faro.analysis.catalog import Table
from faro.analysis.selection import STAGED, Log, stage
from faro.analysis.source import AnalysisError, connect, one, table_keys
from faro.dataset.lock import Lock

NEEDED = STAGED | {"digital_events"}
CONTACTS = "contacts"
DIGITAL_EVENTS = "digital_events"
DIGITAL_SESSIONS = "digital_sessions"
CARD_TRANSACTIONS = "card_transactions"
COMPLAINTS = "complaints"
STREAMS = (CONTACTS, DIGITAL_SESSIONS, DIGITAL_EVENTS, CARD_TRANSACTIONS, COMPLAINTS)
HOURS = 24
# A spike is a day this far above, or below, the median of the same weekday over the weeks before it.
SPIKE = 1.5
DIP = 0.5
SPIKE_WEEKS = 4
QUANTILES = (0.5, 0.9, 0.99)
CONTACT_GROUPS = {
    "channel": "channel",
    "interaction_type": "interaction_type",
    "reason_category": "reason_category",
    "country": "country",
}
TIMING_GROUPS = ("channel", "reason_category")


@dataclass(frozen=True)
class DailyStats:
    days: int
    rows: int
    mean: float
    median: float
    p95: float
    fewest: int
    most: int


@dataclass(frozen=True)
class Period:
    first: date
    last: date
    days: int
    rows: int


@dataclass(frozen=True)
class Shape:
    # None for every country together.
    country: str | None
    rows: int
    fewest: int
    most: int
    # Variance over mean of the 24 hour-of-day counts: about 1 when rows fall on the hours at random.
    dispersion: float | None


@dataclass(frozen=True)
class HourlyStats:
    hours: int
    mean: float
    p95: float
    p99: float
    most: int


@dataclass(frozen=True)
class Spikes:
    # Days with SPIKE_WEEKS of the same weekday before them.
    days: int
    above: int
    below: int
    highest: float | None
    lowest: float | None
    spike_days: tuple[date, ...]


@dataclass(frozen=True)
class Stream:
    name: str
    rows: int
    daily: Breakdown
    year: DailyStats
    periods: tuple[Period, ...]
    weekday: Breakdown
    hour_of_day: Breakdown
    shapes: tuple[Shape, ...]
    hourly: HourlyStats
    spikes: Spikes


@dataclass(frozen=True)
class Volume:
    value: str | None
    rows: int
    busiest_day: int
    busiest_hour: int


@dataclass(frozen=True)
class Distribution:
    rows: int
    mean: float | None
    # At QUANTILES; None when no row has the measure.
    quantiles: tuple[float, ...] | None


@dataclass(frozen=True)
class Timing:
    # None for every contact together.
    value: str | None
    contacts: int
    handle: Distribution
    wait: Distribution


@dataclass(frozen=True)
class Contacts:
    rows: int
    volumes: tuple[tuple[str, tuple[Volume, ...]], ...]
    timings: tuple[tuple[str, tuple[Timing, ...]], ...]
    overall: Timing


@dataclass(frozen=True)
class Digital:
    # Events of development customers, over the whole snapshot.
    named: int
    # Events without a customer, in sessions that name a development customer.
    attributed: int
    sessions: int
    # Sessions naming a development customer and anyone else.
    sessions_naming_others: int
    events_per_session: Distribution
    session_seconds: Distribution


@dataclass(frozen=True)
class Traffic:
    snapshot_id: str
    duckdb_version: str
    business_date: date
    as_of: datetime
    customers: int
    held_out: int
    streams: tuple[Stream, ...]
    contacts: Contacts
    digital: Digital

    def stream(self, name: str) -> Stream:
        return next(s for s in self.streams if s.name == name)

    @property
    def bank_factor(self) -> float:
        return (self.customers + self.held_out) / self.customers


def define_hours(con: duckdb.DuckDBPyConnection) -> None:
    # An hour, like a day, ends at its last instant, so a row at the as-of instant falls in the last hour.
    con.execute(
        "create or replace temp macro clock_hour(ts) as date_trunc('hour', ts - interval 1 microsecond)"
    )


def prepare(con: duckdb.DuckDBPyConnection) -> None:
    define_hours(con)
    con.execute(
        """
        create or replace temp table s_contacts as
        select try_cast(i.interaction_date as timestamp) as ts, c.country, i.channel,
          i.interaction_type, i.reason_category,
          try_cast(i.duration_seconds as double) as duration_seconds,
          try_cast(i.wait_time_seconds as double) as wait_time_seconds
        from raw_call_center_interactions i join development d on d.customer_id = i.customer_id
          join customers c on c.customer_id = i.customer_id
        where try_cast(i.interaction_date as timestamp) <= getvariable('as_of')
        """
    )
    con.execute(
        f"""
        create or replace temp table s_card_transactions as
        select t.transaction_date as ts, c.country
        from transactions t join development d on d.customer_id = t.customer_id
          join customers c on c.customer_id = t.customer_id
        where t.product_type in {sql_list(CARDS)}
        """
    )
    con.execute(
        """
        create or replace temp table s_complaints as
        select k.creation_date as ts, c.country
        from complaints k join development d on d.customer_id = k.customer_id
          join customers c on c.customer_id = k.customer_id
        """
    )
    # Rows without a customer count only in a session that names a development customer, since a session
    # that names no one can't be placed on either side of the split.
    con.execute(
        """
        create or replace temp table session_owners as
        select e.session_id, min(e.customer_id) as customer_id
        from raw_digital_events e join development d on d.customer_id = e.customer_id
        where try_cast(e.event_date as timestamp) <= getvariable('as_of')
        group by 1
        """
    )
    con.execute(
        """
        create or replace temp table s_digital_events as
        select try_cast(e.event_date as timestamp) as ts, e.session_id, c.country,
          e.customer_id is null as attributed
        from raw_digital_events e join session_owners o on o.session_id = e.session_id
          join customers c on c.customer_id = o.customer_id
        where try_cast(e.event_date as timestamp) <= getvariable('as_of')
          and (e.customer_id is null or e.customer_id = o.customer_id)
        """
    )
    con.execute(
        """
        create or replace temp table s_digital_sessions as
        select session_id, min(ts) as ts, any_value(country) as country, count(*) as events,
          epoch(max(ts) - min(ts)) as seconds
        from s_digital_events group by 1
        """
    )


def _grid(source: str) -> str:
    return f"""
        (select g.day, coalesce(c.n, 0) as n
        from (select cast(unnest(generate_series(
            (select cast(business_day(min(ts)) as timestamp) from {source}),
            cast(getvariable('business_date') as timestamp), interval 1 day)) as date) as day) g
          left join (select business_day(ts) as day, count(*) as n from {source} group by 1) c
            on c.day = g.day)
    """


def _year_days() -> str:
    return f"day > getvariable('business_date') - {YEAR}"


def daily_stats(con: duckdb.DuckDBPyConnection, source: str) -> DailyStats:
    days, rows, mean, median, p95, fewest, most = one(
        con,
        "select count(*), coalesce(sum(n), 0), coalesce(avg(n), 0), "
        "coalesce(quantile_cont(n, 0.5), 0), coalesce(quantile_cont(n, 0.95), 0), "
        f"coalesce(min(n), 0), coalesce(max(n), 0) from {_grid(source)} where {_year_days()}",
    )
    return DailyStats(
        days, int(rows), float(mean), float(median), float(p95), int(fewest), int(most)
    )


def periods(con: duckdb.DuckDBPyConnection, source: str) -> tuple[Period, ...]:
    rows = con.execute(
        f"""
        select case when day > getvariable('business_date') - {YEAR} then 1
          when day > getvariable('business_date') - {2 * YEAR} then 2 else 3 end as period,
          min(day), max(day), count(*), sum(n)
        from {_grid(source)} group by 1 order by 1
        """
    ).fetchall()
    return tuple(Period(r[1], r[2], r[3], int(r[4])) for r in rows)


def shapes(hour_of_day: Breakdown) -> tuple[Shape, ...]:
    counts: dict[str | None, list[int]] = {}
    for g in hour_of_day.groups:
        hour, country = int(str(g.values[0])), g.values[1]
        for key in (None, country):
            counts.setdefault(key, [0] * HOURS)[hour] += g.rows
    result = []
    for key in sorted(counts, key=lambda k: (k is not None, k or "")):
        n = counts[key]
        mean = sum(n) / HOURS
        variance = sum((x - mean) ** 2 for x in n) / (HOURS - 1)
        result.append(
            Shape(key, sum(n), min(n), max(n), variance / mean if mean else None)
        )
    return tuple(result)


def hourly_stats(con: duckdb.DuckDBPyConnection, source: str) -> HourlyStats:
    hours, mean, p95, p99, most = one(
        con,
        f"""
        select count(*), avg(coalesce(c.n, 0)), quantile_cont(coalesce(c.n, 0), 0.95),
          quantile_cont(coalesce(c.n, 0), 0.99), max(coalesce(c.n, 0))
        from (select unnest(generate_series(
            date_trunc('hour', getvariable('as_of') - to_days({YEAR})),
            clock_hour(getvariable('as_of')), interval 1 hour)) as hour) g
          left join (select clock_hour(ts) as hour, count(*) as n from {source}
            where within(ts, {YEAR}) group by 1) c on c.hour = g.hour
        """,
    )
    return HourlyStats(hours, float(mean), float(p95), float(p99), int(most))


def spikes(con: duckdb.DuckDBPyConnection, source: str) -> Spikes:
    ratios = f"""
        (select day, n / median(n) over w as ratio, count(*) over w as weeks
        from {_grid(source)}
        window w as (partition by isodow(day) order by day
          rows between {SPIKE_WEEKS} preceding and 1 preceding))
    """
    days, above, below, highest, lowest = one(
        con,
        f"select count(*), count(*) filter (where ratio > {SPIKE}), "
        f"count(*) filter (where ratio < {DIP}), max(ratio), min(ratio) from {ratios} "
        f"where weeks = {SPIKE_WEEKS} and ratio is not null and isfinite(ratio)",
    )
    spike_days = con.execute(
        f"select day from {ratios} where weeks = {SPIKE_WEEKS} and isfinite(ratio) "
        f"and ratio > {SPIKE} order by 1"
    ).fetchall()
    return Spikes(
        days,
        above,
        below,
        None if highest is None else float(highest),
        None if lowest is None else float(lowest),
        tuple(r[0] for r in spike_days),
    )


def stream(con: duckdb.DuckDBPyConnection, name: str) -> Stream:
    source = f"s_{name}"
    year = f"within(ts, {YEAR})"
    daily = con.execute(
        f"select cast(day as varchar), n from {_grid(source)} order by 1"
    ).fetchall()
    hour_of_day = breakdown(
        con,
        source,
        {
            "hour": "lpad(cast(hour(clock_hour(ts)) as varchar), 2, '0')",
            "country": "country",
        },
        where=year,
    )
    return Stream(
        name=name,
        rows=one(con, f"select count(*) from {source}")[0],
        daily=Breakdown(("day",), tuple(Group((d,), int(n)) for d, n in daily)),
        year=daily_stats(con, source),
        periods=periods(con, source),
        weekday=breakdown(
            con, source, {"weekday": "isodow(business_day(ts))"}, where=year
        ),
        hour_of_day=hour_of_day,
        shapes=shapes(hour_of_day),
        hourly=hourly_stats(con, source),
        spikes=spikes(con, source),
    )


def volumes(con: duckdb.DuckDBPyConnection, expr: str) -> tuple[Volume, ...]:
    base = f"(select {expr} as g, ts from s_contacts where within(ts, {YEAR}))"
    rows = con.execute(
        f"""
        select r.g, r.n, d.n, h.n
        from (select g, count(*) as n from {base} group by 1) r
          join (select g, max(n) as n from (select g, business_day(ts), count(*) as n from {base}
            group by all) group by 1) d on d.g is not distinct from r.g
          join (select g, max(n) as n from (select g, clock_hour(ts), count(*) as n from {base}
            group by all) group by 1) h on h.g is not distinct from r.g
        order by 1
        """
    ).fetchall()
    return tuple(Volume(g, n, day, hour) for g, n, day, hour in rows)


def _distribution(
    rows: int, mean: float | None, quantiles: Sequence[float] | None
) -> Distribution:
    return Distribution(
        rows,
        None if mean is None else float(mean),
        None if quantiles is None else tuple(float(q) for q in quantiles),
    )


def timings(con: duckdb.DuckDBPyConnection, expr: str) -> tuple[Timing, ...]:
    rows = con.execute(
        f"""
        select {expr}, count(*), count(duration_seconds), avg(duration_seconds),
          quantile_cont(duration_seconds, {list(QUANTILES)}), count(wait_time_seconds),
          avg(wait_time_seconds), quantile_cont(wait_time_seconds, {list(QUANTILES)})
        from s_contacts where within(ts, {YEAR}) group by 1 order by 1
        """
    ).fetchall()
    return tuple(
        Timing(r[0], r[1], _distribution(*r[2:5]), _distribution(*r[5:8])) for r in rows
    )


def contacts(con: duckdb.DuckDBPyConnection) -> Contacts:
    (overall,) = timings(con, "cast(null as varchar)")
    return Contacts(
        rows=one(con, f"select count(*) from s_contacts where within(ts, {YEAR})")[0],
        volumes=tuple(
            (key, volumes(con, expr)) for key, expr in CONTACT_GROUPS.items()
        ),
        timings=tuple((key, timings(con, key)) for key in TIMING_GROUPS),
        overall=overall,
    )


def digital(con: duckdb.DuckDBPyConnection) -> Digital:
    named, attributed = one(
        con,
        "select count(*) filter (where not attributed), count(*) filter (where attributed) "
        "from s_digital_events",
    )
    others: int = one(
        con,
        """
        select count(distinct o.session_id)
        from session_owners o join raw_digital_events e on e.session_id = o.session_id
        where e.customer_id <> o.customer_id
          and try_cast(e.event_date as timestamp) <= getvariable('as_of')
        """,
    )[0]
    sessions_year = f"(select * from s_digital_sessions where within(ts, {YEAR}))"
    events = one(
        con,
        f"select count(*), avg(events), quantile_cont(events, {list(QUANTILES)}) "
        f"from {sessions_year}",
    )
    seconds = one(
        con,
        f"select count(*), avg(seconds), quantile_cont(seconds, {list(QUANTILES)}) "
        f"from {sessions_year}",
    )
    return Digital(
        named=named,
        attributed=attributed,
        sessions=one(con, "select count(*) from s_digital_sessions")[0],
        sessions_naming_others=others,
        events_per_session=_distribution(*events),
        session_seconds=_distribution(*seconds),
    )


def traffic(
    lock: Lock,
    root: Path,
    tables: Sequence[Table],
    business_date: date,
    as_of: datetime,
    log: Log = print,
) -> Traffic:
    catalog = {t.name: t for t in tables}
    missing_tables = sorted(NEEDED - catalog.keys())
    if missing_tables:
        raise AnalysisError(f"the traffic analysis needs {', '.join(missing_tables)}")
    raw = [catalog[name] for name in sorted(NEEDED)]
    con = connect(root, raw, {t.name: table_keys(lock, t) for t in raw})
    try:
        log(f"Staging the tables as of {as_of}")
        stage(con, as_of)
        log("Setting the held-out customers aside")
        development, set_aside = split(con)
        define_days(con, business_date)
        prepare(con)
        log("Measuring the development customers' traffic")
        result = Traffic(
            snapshot_id=lock.snapshot_id,
            duckdb_version=duckdb.__version__,
            business_date=business_date,
            as_of=as_of,
            customers=development,
            held_out=set_aside,
            streams=tuple(stream(con, name) for name in STREAMS),
            contacts=contacts(con),
            digital=digital(con),
        )
    finally:
        con.close()
    return result
