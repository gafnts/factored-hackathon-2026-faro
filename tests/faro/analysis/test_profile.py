"""
The profile finds each problem planted in the fixture snapshot, and nothing else (PRB-03, DML-02, DML-03).
"""

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from faro import clock
from faro.analysis import profile as profiling
from faro.analysis.catalog import Table, table
from faro.analysis.profile import Profile, TableProfile, profile
from faro.analysis.source import AnalysisError
from faro.dataset.lock import Lock


@pytest.fixture
def result(
    lock: Lock, root: Path, tables: tuple[Table, ...], monkeypatch: pytest.MonkeyPatch
) -> Profile:
    # Every fixture row is recent, so count them all as settled.
    monkeypatch.setattr(clock, "SETTLED_DAYS", 0)
    monkeypatch.setitem(profiling.COUNTED, "branches", ("zone",))
    return profile(lock, root, tables, log=lambda _: None)


def by_name(result: Profile, name: str) -> TableProfile:
    return next(t for t in result.tables if t.name == name)


def test_counts_rows_files_and_header_versions(result: Profile) -> None:
    payments = by_name(result, "payments")

    assert (payments.rows, payments.files, payments.header_variants) == (8, 3, 2)
    assert payments.unexpected_columns == ("note",)
    assert payments.missing_columns == ()


def test_tells_duplicates_apart(result: Profile) -> None:
    branches = by_name(result, "branches").duplicates
    payments = by_name(result, "payments").duplicates

    assert (branches.exact, branches.redelivered, branches.conflicting) == (1, 0, 0)
    assert (payments.exact, payments.redelivered, payments.conflicting) == (0, 1, 1)
    assert payments.repeated_keys == 2


def test_finds_a_unique_value_shared_by_two_keys(result: Profile) -> None:
    assert by_name(result, "branches").repeated_unique == {"code": 1}


def test_checks_each_column_against_the_dictionary(result: Profile) -> None:
    branches = {c.name: c for c in by_name(result, "branches").columns}
    payments = {c.name: c for c in by_name(result, "payments").columns}

    assert branches["opened"].nulls == 2
    assert branches["opened"].invalid == 1
    assert branches["zone"].padded == 2
    assert branches["zone"].outside == 3
    assert branches["zone"].outside_values == (" Rural", "Urbana")
    assert payments["amount"].invalid == 1
    assert (payments["amount"].low, payments["amount"].high) == ("10.00", "99.00")
    assert payments["approved"].invalid == 1
    assert payments["approved"].spellings == ("False", "True", "maybe")
    assert payments["branch_id"].nulls == 2
    assert payments["payment_id"].longest == 2


def test_counts_references_that_point_nowhere(result: Profile) -> None:
    assert by_name(result, "payments").orphans == {"branch_id": 1}
    assert by_name(result, "receipts").orphans == {"payment_id": 1}


def test_profiles_arrival(result: Profile) -> None:
    arrival = by_name(result, "payments").arrival

    assert arrival is not None
    assert (arrival.first_partition, arrival.last_partition) == (
        date(2026, 6, 14),
        date(2026, 6, 17),
    )
    assert arrival.missing_partitions == 1
    assert arrival.misfiled == 2
    assert arrival.next_day_until == "03:00:00"
    assert arrival.lags == {
        "dated after its process date": 1,
        "same day": 5,
        "1 day late": 1,
        "2 to 7 days late": 1,
        "8 to 30 days late": 0,
        "over 30 days late": 0,
    }
    assert arrival.lag_p99 == 2
    assert arrival.last_complete_day == date(2026, 6, 15)
    assert len(arrival.recent) == profiling.RECENT_DAYS


def test_dates_a_table_through_its_reference(result: Profile) -> None:
    arrival = by_name(result, "receipts").arrival

    assert arrival is not None
    assert arrival.undated == 1
    assert arrival.first_event == date(2026, 6, 14)
    assert arrival.last_complete_day == date(2026, 6, 14)


def test_the_business_date_is_the_earliest_last_complete_day(result: Profile) -> None:
    assert result.business_date == date(2026, 6, 14)
    assert by_name(result, "branches").arrival is None


def test_reads_as_of_the_earliest_processing_day_cutoff(result: Profile) -> None:
    # Receipts have no events past midnight, so their processing day closes at midnight.
    assert result.as_of == datetime(2026, 6, 15, 0, 0)
    payments_only = replace(
        result, tables=tuple(t for t in result.tables if t.name != "receipts")
    )
    assert payments_only.as_of == datetime(2026, 6, 16, 3, 0)
    assert replace(result, tables=()).as_of is None


def test_skips_the_checks_whose_tables_the_catalog_lacks(result: Profile) -> None:
    assert result.attribution is None
    assert result.cutoffs == ()


def test_counts_listed_values(result: Profile) -> None:
    assert by_name(result, "branches").value_counts == {
        "zone": {" Rural": 2, "Urban": 1, "Urbana": 1}
    }


def test_refuses_a_table_the_lock_doesnt_hold(
    lock: Lock, root: Path, tables: tuple[Table, ...]
) -> None:
    ghost = table("ghosts", "ghost_id VARCHAR(5) NOT NULL", key=("ghost_id",))

    with pytest.raises(AnalysisError, match="no files for ghosts"):
        profile(lock, root, (*tables, ghost), log=lambda _: None)
