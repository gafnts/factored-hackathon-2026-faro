"""
The traffic analysis reads development customers only (DML-09), counts days and hours the way the windows do,
and places digital events without a customer only through their session.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from faro.analysis import catalog
from faro.analysis.cards import Breakdown, Group, define_days
from faro.analysis.selection import define_window
from faro.analysis.source import AnalysisError
from faro.analysis.traffic import (
    CARD_TRANSACTIONS,
    COMPLAINTS,
    CONTACTS,
    DIGITAL_EVENTS,
    DIGITAL_SESSIONS,
    HOURS,
    Traffic,
    define_hours,
    shapes,
    spikes,
    traffic,
)
from faro.dataset.lock import Lock

TABLES = (
    catalog.CUSTOMERS,
    catalog.PRODUCTS,
    catalog.TRANSACTIONS,
    catalog.COMPLAINTS,
    catalog.CALL_CENTER_INTERACTIONS,
    catalog.SATISFACTION_SURVEYS,
    catalog.DIGITAL_EVENTS,
)
BUSINESS_DATE = date(2026, 6, 17)
AS_OF = datetime(2026, 6, 18, 6, 0)


def test_reads_only_development_customers(traffic_result: Traffic) -> None:
    # DML-09: A1's contact, transaction, complaint, and digital session count nowhere.
    assert (traffic_result.customers, traffic_result.held_out) == (2, 1)
    assert traffic_result.stream(CONTACTS).rows == 4
    assert traffic_result.stream(CARD_TRANSACTIONS).rows == 1
    assert traffic_result.stream(COMPLAINTS).rows == 1
    assert traffic_result.stream(DIGITAL_SESSIONS).rows == 2
    assert traffic_result.bank_factor == 1.5


def test_places_events_without_a_customer_through_their_session(
    traffic_result: Traffic,
) -> None:
    digital = traffic_result.digital

    # E2 joins A3's session; W2 names no one and W3 is held out, so neither is read.
    assert (digital.named, digital.attributed) == (2, 1)
    assert traffic_result.stream(DIGITAL_EVENTS).rows == 3
    # W1 also names A4, whose event there doesn't count as A3's.
    assert digital.sessions_naming_others == 1


def test_counts_every_day_from_the_first_to_the_business_date(
    traffic_result: Traffic,
) -> None:
    contacts = traffic_result.stream(CONTACTS)
    days = [date.fromisoformat(str(g.values[0])) for g in contacts.daily.groups]

    assert days[0] == date(2025, 1, 1)
    assert days[-1] == date(2026, 6, 17)
    assert len(days) == (days[-1] - days[0]).days + 1
    assert sum(g.rows for g in contacts.daily.groups) == 4
    assert (contacts.year.days, contacts.year.rows, contacts.year.fewest) == (365, 3, 0)
    # I3, at the as-of instant, belongs to the business date, like I1 and I2.
    assert contacts.year.most == 3


def test_counts_every_clock_hour_of_the_year(traffic_result: Traffic) -> None:
    contacts = traffic_result.stream(CONTACTS)

    assert contacts.hourly.hours == 365 * HOURS
    assert contacts.hourly.most == 1
    # An hour ends at its last instant: 10:00 falls in the hour from 09:00, and the as-of instant in the
    # last hour before it.
    assert {str(g.values[0]) for g in contacts.hour_of_day.groups} == {"05", "09", "10"}


def test_breaks_contacts_down_over_the_last_year(traffic_result: Traffic) -> None:
    volumes = dict(traffic_result.contacts.volumes)
    types = {v.value: v.rows for v in volumes["interaction_type"]}

    assert traffic_result.contacts.rows == 3
    assert types == {"Chat": 1, "Inbound Call": 2}
    overall = traffic_result.contacts.overall
    assert (overall.handle.rows, overall.handle.mean) == (2, 250.0)
    assert (overall.wait.rows, overall.wait.mean) == (1, 60.0)
    chat = next(
        t
        for t in dict(traffic_result.contacts.timings)["channel"]
        if t.value == "WhatsApp"
    )
    assert (chat.handle.rows, chat.handle.mean, chat.handle.quantiles) == (
        0,
        None,
        None,
    )


def test_dispersion_reads_zero_when_flat_and_high_when_concentrated() -> None:
    flat = Breakdown(
        ("hour", "country"),
        tuple(Group((f"{h:02d}", "Mexico"), 10) for h in range(HOURS)),
    )
    concentrated = Breakdown(("hour", "country"), (Group(("12", "Mexico"), 240),))

    assert [s.dispersion for s in shapes(flat)] == [0.0, 0.0]
    (overall, mexico) = shapes(concentrated)
    assert overall.country is None and mexico.country == "Mexico"
    assert (overall.fewest, overall.most) == (0, 240)
    assert overall.dispersion == pytest.approx(240.0)


def test_compares_each_day_with_the_same_weekday_before_it() -> None:
    con = duckdb.connect()
    define_window(con, datetime(2026, 6, 16, 6, 0))
    define_days(con, date(2026, 6, 15))
    define_hours(con)
    mondays = [date(2026, 5, 11) + timedelta(weeks=w) for w in range(6)]
    rows = [(m, 20 if m == mondays[-1] else 10) for m in mondays]
    con.execute("create table s_test (ts timestamp, country varchar)")
    for day, n in rows:
        con.executemany(
            "insert into s_test values (?, 'Mexico')",
            [(datetime.combine(day, datetime.min.time()) + timedelta(hours=12),)] * n,
        )

    result = spikes(con, "s_test")

    # Only the last two Mondays have four Mondays before them; other weekdays have no rows to compare.
    assert (result.days, result.above, result.below) == (2, 1, 0)
    assert result.highest == 2.0
    assert result.spike_days == (mondays[-1],)


def test_runs_as_of_the_instant_it_is_given(traffic_bank: tuple[Lock, Path]) -> None:
    lock, root = traffic_bank
    later = traffic(
        lock,
        root,
        TABLES,
        BUSINESS_DATE + timedelta(days=1),
        AS_OF + timedelta(days=1),
        log=lambda _: None,
    )

    # I6 and E8 fall within the later instant.
    assert later.stream(CONTACTS).rows == 5
    assert later.digital.named == 3


def test_needs_the_tables_it_reads(traffic_bank: tuple[Lock, Path]) -> None:
    lock, root = traffic_bank
    with pytest.raises(AnalysisError, match="needs digital_events"):
        traffic(lock, root, TABLES[:-1], BUSINESS_DATE, AS_OF, log=lambda _: None)
