"""
A bronze connection sees one side of the split: its customers, and only their cards and transactions.
"""

from collections.abc import Iterator
from datetime import date, datetime

import duckdb
import pytest

from faro.evaluation import bronze
from faro.split import held_out

from .bank import EXAMPLE_CUSTOMER, HELD_OUT_CUSTOMER, Bank

pytestmark = pytest.mark.xdist_group("evaluation_bank")


@pytest.fixture(params=bronze.SIDES)
def side(request: pytest.FixtureRequest) -> str:
    side: str = request.param
    return side


@pytest.fixture
def con(bank: Bank, side: str) -> Iterator[duckdb.DuckDBPyConnection]:
    with bronze.connect(bank.database, side) as con:
        yield con


def owners(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {
        row[0] for row in con.execute(f"select customer_id from {table}").fetchall()
    }


def test_each_table_holds_its_sides_customers_only(
    con: duckdb.DuckDBPyConnection, side: str
) -> None:
    for table in bronze.TABLES:
        found = owners(con, table)
        assert found
        assert {held_out(c) for c in found} == {side == "held_out"}


def test_the_sides_split_the_bank(bank: Bank) -> None:
    with (
        bronze.connect(bank.database, "development") as development,
        bronze.connect(bank.database, "held_out") as held,
        duckdb.connect(str(bank.database), read_only=True) as whole,
    ):
        for table in bronze.TABLES:
            everyone = owners(whole, f"bronze.{table}")
            assert owners(development, table) | owners(held, table) == everyone
            assert not owners(development, table) & owners(held, table)
        assert EXAMPLE_CUSTOMER in owners(development, "transactions")
        assert HELD_OUT_CUSTOMER in owners(held, "transactions")


def test_a_view_keeps_bronzes_columns(bank: Bank) -> None:
    with (
        bronze.connect(bank.database, "development") as con,
        duckdb.connect(str(bank.database), read_only=True) as whole,
    ):
        for table in bronze.TABLES:
            ours = con.execute(f"select * from {table} limit 0").description
            theirs = whole.execute(f"select * from bronze.{table} limit 0").description
            assert [c[0] for c in ours] == [c[0] for c in theirs]


def test_the_stamp_and_clock_are_the_builds_own(bank: Bank) -> None:
    with bronze.connect(bank.database, "development") as con:
        built, clock = bronze.stamp(con)

    assert built == bank.stamp
    assert clock == (date(2026, 6, 17), datetime(2026, 6, 18, 6, 0))


def test_no_other_side_is_opened(bank: Bank) -> None:
    with pytest.raises(ValueError, match="no side named everyone"):
        bronze.connect(bank.database, "everyone")
