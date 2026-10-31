"""
Silver holds card support as of the as-of instant (ADR-0004, State): what was registered, opened, and dated by then,
flagged and never corrected, split by the rule the analysis and the evaluation share (DML-09), with each change it
makes to a delivered value counted (DML-03).
"""

import duckdb
import pytest

from faro import split
from faro.pipeline import runner

from .conftest import Built

pytestmark = pytest.mark.xdist_group("pipeline")

WARNINGS = {
    "silver_card_transactions_after_card_expiration": 1,
    "silver_card_transactions_transaction_country_changed": 2,
    "silver_cards_active_past_expiration": 1,
    "silver_cards_last_transaction_date_disagrees": 13,
    "silver_cards_updated_after_as_of": 1,
    "silver_customers_updated_after_as_of": 1,
}


def _ids(base: Built, sql: str) -> list[str]:
    with duckdb.connect(str(base.space.database), read_only=True) as con:
        return [str(row[0]) for row in con.execute(sql).fetchall()]


def test_the_split_in_sql_is_the_shared_rules(base: Built) -> None:
    with duckdb.connect(str(base.space.database), read_only=True) as con:
        rows = con.execute(
            "select customer_id, held_out from silver.customers"
        ).fetchall()

    assert rows
    assert {held for _, held in rows} == {True, False}
    assert all(held == split.held_out(customer) for customer, held in rows)


def test_silver_holds_what_was_registered_opened_and_dated_by_the_as_of_instant(
    base: Built,
) -> None:
    customers = _ids(base, "select customer_id from silver.customers order by 1")
    cards = _ids(base, "select product_id from silver.cards order by 1")
    transactions = _ids(
        base, "select transaction_id from silver.card_transactions order by 1"
    )

    assert customers == [f"CLI-TEAM{n:08d}" for n in range(1, 10)]
    assert cards == [
        f"PRD-TEAM{n:08d}" for n in (11, 12, 21, 22, 31, 32, 35, 41, 51, 52, 61, 71, 81)
    ]
    assert "TRX-TEAM0000000000000315" in transactions
    for after_or_off_card in (305, 306, 307, 308, 351):
        assert f"TRX-TEAM{after_or_off_card:016d}" not in transactions


def test_silver_counts_its_warnings_and_changes_in_rows(base: Built) -> None:
    warned = {
        runner.node_name(r): r["failures"]
        for r in base.results
        if r["status"] == "warn" and runner.node_name(r).startswith("silver_")
    }

    assert warned == WARNINGS


def test_silvers_unit_tests_ran(base: Built) -> None:
    unit = [r for r in base.results if str(r["unique_id"]).startswith("unit_test.")]

    assert len(unit) == 3
    assert {r["status"] for r in unit} == {"pass"}
