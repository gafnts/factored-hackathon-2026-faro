"""
ADR-0003's business clock, which the profile and the pipeline share (ADR-0006, Freshness).
"""

import json
from datetime import date, datetime, time
from pathlib import Path

from faro import clock

PROFILE = Path(__file__).resolve().parents[2] / "docs" / "analysis" / "profiling.json"


def test_the_p99_lag_reads_only_rows_that_arrived_on_or_after_their_day() -> None:
    assert clock.lag_p99({-1: 500, 0: 90, 1: 9, 2: 1}) == 1
    assert clock.lag_p99({0: 99, 5: 1}) == 0
    assert clock.lag_p99({-1: 3}) is None
    assert clock.lag_p99({}) is None


def test_a_table_without_settled_rows_has_no_complete_day() -> None:
    assert clock.last_complete_day(date(2026, 6, 17), 2) == date(2026, 6, 15)
    assert clock.last_complete_day(date(2026, 6, 17), None) is None
    assert clock.settled_before(date(2026, 6, 17)) == date(2026, 5, 17)


def test_the_business_date_is_the_earliest_complete_day() -> None:
    days = [date(2026, 6, 17), None, date(2026, 6, 16)]

    assert clock.business_date(days) == date(2026, 6, 16)
    assert clock.business_date([None, None]) is None


def test_the_as_of_instant_is_the_earliest_cutoff_the_next_morning() -> None:
    cutoffs = [time(8), time(6), time(23, 59, 58)]

    assert clock.as_of(date(2026, 6, 17), cutoffs) == datetime(2026, 6, 18, 6)
    assert clock.as_of(date(2026, 6, 17), [time(8), None]) == datetime(2026, 6, 18)


def test_the_rule_gives_the_published_profile_its_clock() -> None:
    profile = json.loads(PROFILE.read_text())
    arrivals = [t["arrival"] for t in profile["tables"] if t["arrival"]]
    days = [
        clock.last_complete_day(date.fromisoformat(a["last_partition"]), a["lag_p99"])
        for a in arrivals
    ]
    business = clock.business_date(days)
    assert business is not None

    as_of = clock.as_of(
        business,
        (time.fromisoformat(a["next_day_until"]) for a in arrivals),
    )

    assert profile["snapshot_id"] == "b3b8b248f604ef9a"
    assert [d.isoformat() if d else None for d in days] == [
        a["last_complete_day"] for a in arrivals
    ]
    assert (business, as_of) == (date(2026, 6, 17), datetime(2026, 6, 18, 6))
    assert (profile["business_date"], profile["as_of"]) == (
        "2026-06-17",
        "2026-06-18 06:00:00",
    )
