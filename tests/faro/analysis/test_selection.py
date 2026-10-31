"""
The gates of ADR-0003 measure what its Measurement section says, on a snapshot written by the team (PRB-05).
"""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from faro.analysis.candidates import (
    ACCOUNT_INQUIRIES,
    CARD_SUPPORT,
    CREDIT_ELIGIBILITY,
    DISPUTE_INTAKE,
    Rule,
)
from faro.analysis.catalog import (
    CALL_CENTER_INTERACTIONS,
    COMPLAINTS,
    CUSTOMERS,
    PRODUCTS,
    SATISFACTION_SURVEYS,
    TRANSACTIONS,
)
from faro.analysis.selection import (
    FieldPopulation,
    Selection,
    check_rules,
    customers_in_state,
    field_populations,
    select,
)
from faro.analysis.source import AnalysisError, one
from faro.dataset.lock import Lock

AS_OF = datetime(2026, 6, 18, 6, 0)
TABLES = (
    CUSTOMERS,
    PRODUCTS,
    TRANSACTIONS,
    COMPLAINTS,
    CALL_CENTER_INTERACTIONS,
    SATISFACTION_SURVEYS,
)
CATALOG = {t.name: t for t in TABLES}


def by_field(fields: tuple[FieldPopulation, ...]) -> dict[str, tuple[int, int]]:
    return {f"{f.table}.{f.name}": (f.populated, f.rows) for f in fields}


def gates_for(
    staged: duckdb.DuckDBPyConnection, key: str
) -> dict[str, tuple[int, int]]:
    candidate = next(
        c
        for c in (ACCOUNT_INQUIRIES, CARD_SUPPORT, DISPUTE_INTAKE, CREDIT_ELIGIBILITY)
        if c.key == key
    )
    return by_field(
        tuple(f for s in candidate.scopes for f in field_populations(staged, s))
    )


def test_stages_only_rows_dated_by_the_as_of_instant(
    staged: duckdb.DuckDBPyConnection,
) -> None:
    counts = one(
        staged,
        "select (select count(*) from customers), (select count(*) from transactions), "
        "(select count(*) from complaints)",
    )
    assert counts == (2, 4, 2)
    assert one(staged, "select count(*) from interactions") == (3,)
    assert one(staged, "select count(*) from surveys") == (3,)


def test_stages_the_card_fields_typed(staged: duckdb.DuckDBPyConnection) -> None:
    assert one(
        staged,
        "select product_number, typeof(last_transaction_date), typeof(last_updated) "
        "from products where product_id = 'P1'",
    ) == ("4000000000000002", "TIMESTAMP", "TIMESTAMP")
    assert one(
        staged, "select amount_usd from transactions where transaction_id = 'T1'"
    ) == (Decimal("1.25"),)
    assert one(
        staged, "select affected_product_id from complaints where complaint_id = 'K1'"
    ) == ("P1",)
    assert one(
        staged, "select customer_id from interactions where interaction_id = 'I1'"
    ) == ("C1",)


def test_compares_countries_without_accents(staged: duckdb.DuckDBPyConnection) -> None:
    assert one(staged, "select country from customers where customer_id = 'C1'") == (
        "Mexico",
    )


def test_windows_end_at_the_as_of_instant(staged: duckdb.DuckDBPyConnection) -> None:
    within = staged.execute(
        "select transaction_id from transactions where within(transaction_date, 60) order by 1"
    ).fetchall()
    assert within == [("T1",), ("T2",), ("T4",)]


def test_measures_a_field_only_in_the_rows_the_dictionary_names(
    staged: duckdb.DuckDBPyConnection,
) -> None:
    fields = gates_for(staged, "card_support")

    assert fields["products.credit_limit"] == (1, 1)
    assert fields["products.expiration_date"] == (1, 2)
    assert fields["transactions.merchant_name"] == (1, 2)
    assert fields["transactions.response_code"] == (2, 2)


def test_a_field_with_no_rows_to_measure_fails(
    staged: duckdb.DuckDBPyConnection,
) -> None:
    fields = gates_for(staged, "account_inquiries")
    merchant = FieldPopulation("transactions", "merchant_name", 0, 0)

    assert fields["transactions.merchant_name"] == (0, 0)
    assert merchant.share is None
    assert not merchant.passes


def test_reads_disputes_and_credit_in_their_own_rows(
    staged: duckdb.DuckDBPyConnection,
) -> None:
    disputes = gates_for(staged, "disputes")
    credit = gates_for(staged, "credit")

    assert disputes["transactions.fraud_score"] == (1, 1)
    assert disputes["complaints.claimed_amount"] == (1, 2)
    assert credit["customers.credit_score"] == (1, 2)
    assert credit["products.credit_limit"] == (1, 2)
    assert credit["products.days_past_due"] == (2, 2)


def test_counts_each_customer_in_the_normal_path_state_once(
    staged: duckdb.DuckDBPyConnection,
) -> None:
    assert customers_in_state(staged, CARD_SUPPORT) == 1
    assert customers_in_state(staged, ACCOUNT_INQUIRIES) == 1
    assert customers_in_state(staged, DISPUTE_INTAKE) == 1
    assert customers_in_state(staged, CREDIT_ELIGIBILITY) == 1


def test_rules_must_read_the_dictionary_through_declared_references() -> None:
    assert check_rules(CARD_SUPPORT, CATALOG).passes
    broken = replace(
        CARD_SUPPORT,
        rules=(
            Rule("Unknown field", ("transactions.card_pin",)),
            Rule("Not a reference", ("transactions.amount",), ("transactions.amount",)),
            Rule("Fine", ("products.product_status",)),
        ),
    )
    check = check_rules(broken, CATALOG)

    assert check.unknown_fields == ("transactions.card_pin",)
    assert check.unknown_references == ("transactions.amount",)
    assert not check.passes


def test_passes_without_e2_which_the_revisit_sets_aside(selection: Selection) -> None:
    card, disputes = selection.candidates[1], selection.candidates[2]
    flat = replace(card, signal=disputes.signal)

    assert flat.verdicts == {"f1": True, "f2": True, "e1": True, "e2": False}
    assert flat.passes
    assert not disputes.passes


def test_selects_as_of_the_instant_it_is_given(bank: tuple[Lock, Path]) -> None:
    lock, root = bank
    result = select(lock, root, TABLES, date(2026, 6, 17), AS_OF, log=lambda _: None)

    assert isinstance(result, Selection)
    assert [c.key for c in result.candidates] == [
        "account_inquiries",
        "card_support",
        "disputes",
        "credit",
    ]
    assert [(c.f1, c.f2, c.e1, c.e2) for c in result.candidates] == [
        (False, False, True, False),
        (False, False, True, False),
        (False, False, True, False),
        (False, False, True, False),
    ]
    assert result.candidates[0].signal is None
    assert "transactions" not in result.complaints_reference
    # T3 is past the as-of instant, so only T1 (scored 1.20) and T2 (unscored, fraud) count.
    assert [(b.low, b.transactions, b.fraud) for b in result.fraud_by_score] == [
        (0, 1, 0),
        (None, 1, 1),
    ]
    assert result.top_legitimate_score == 1.2


def test_needs_the_tables_it_stages(bank: tuple[Lock, Path]) -> None:
    lock, root = bank
    with pytest.raises(AnalysisError, match="needs complaints, satisfaction_surveys"):
        select(
            lock,
            root,
            TABLES[:3] + TABLES[4:5],
            date(2026, 6, 17),
            AS_OF,
            log=lambda _: None,
        )
