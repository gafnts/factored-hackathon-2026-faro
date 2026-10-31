"""
The card support analysis reads only development customers (DML-09) and measures what the policy and the
evaluation ADR will cite, on a card bank written by the team.
"""

from datetime import date, datetime
from pathlib import Path

import duckdb
import numpy as np
import pytest

from faro.analysis.cards import (
    Breakdown,
    CardDates,
    CardSupport,
    Conversion,
    LastTransaction,
    Recency,
    TransactionDates,
    Window,
    activity,
    balances,
    card_support,
    context,
    cramers_v,
    currency,
    dates,
    declines,
    fraud,
    holders,
    missing,
)
from faro.analysis.catalog import (
    CALL_CENTER_INTERACTIONS,
    COMPLAINTS,
    CUSTOMERS,
    DAILY_EXCHANGE_RATES,
    PRODUCTS,
    SATISFACTION_SURVEYS,
    TRANSACTIONS,
)
from faro.analysis.source import AnalysisError, one
from faro.dataset.lock import Lock

BUSINESS_DATE = date(2026, 6, 17)
AS_OF = datetime(2026, 6, 18, 6, 0)
TABLES = (
    CUSTOMERS,
    PRODUCTS,
    TRANSACTIONS,
    COMPLAINTS,
    CALL_CENTER_INTERACTIONS,
    SATISFACTION_SURVEYS,
    DAILY_EXCHANGE_RATES,
)


def groups(b: Breakdown) -> dict[tuple[str | None, ...], int | tuple[int, int | None]]:
    return {g.values: g.rows if g.hits is None else (g.rows, g.hits) for g in b.groups}


def test_reads_only_development_customers(
    card_staged: duckdb.DuckDBPyConnection,
) -> None:
    assert card_staged.execute(
        "select customer_id from development order by 1"
    ).fetchall() == [("A3",), ("A4",), ("A5",)]
    assert card_staged.execute(
        "select product_id from cards order by 1"
    ).fetchall() == [("K1",), ("K2",), ("K3",), ("K4",), ("K5",)]
    assert card_staged.execute(
        "select transaction_id from card_transactions order by 1"
    ).fetchall() == [("T1",), ("T2",), ("T3",), ("T4",), ("T5",)]


def test_counts_cards_and_their_holders(card_staged: duckdb.DuckDBPyConnection) -> None:
    h = holders(card_staged)

    assert h.customers == 3
    assert groups(h.status) == {
        ("Tarjeta Crédito", "Active"): 3,
        ("Tarjeta Débito", "Active"): 1,
        ("Tarjeta Débito", "Blocked"): 1,
    }
    assert groups(h.cards_per_customer) == {("1",): 2, ("3",): 1}
    assert groups(h.active_per_customer) == {
        ("Tarjeta Crédito", "1"): 1,
        ("Tarjeta Crédito", "2"): 1,
        ("Tarjeta Débito", "1"): 1,
    }
    assert groups(h.by_country_segment) == {
        ("Argentina", "Premium"): 1,
        ("Colombia", "Plus"): 1,
        ("Mexico", "Basic"): 1,
    }
    assert groups(h.holder_status) == {("Active",): 3, ("Closed",): 1}


def test_describes_card_numbers_without_reading_them_out(
    card_staged: duckdb.DuckDBPyConnection,
) -> None:
    numbers = holders(card_staged).numbers

    assert (numbers.cards, numbers.distinct, numbers.digits_only) == (5, 5, 5)
    assert numbers.lengths == (16,)
    assert groups(numbers.leading_digits) == {("4",): 5}
    assert numbers.luhn_valid == 2
    assert (
        numbers.shared_last_four,
        numbers.shared_last_four_active,
        numbers.shared_last_four_active_same_type,
    ) == (1, 1, 1)


def test_measures_activity_by_the_day_the_windows_use(
    card_staged: duckdb.DuckDBPyConnection,
) -> None:
    a = activity(card_staged)

    assert a.transactions == 5
    # T2, at 05:59 on the 18th, belongs to the business date.
    assert groups(a.daily) == {
        ("2025-01-01",): 1,
        ("2026-03-01",): 1,
        ("2026-06-10",): 1,
        ("2026-06-17",): 2,
    }
    assert groups(a.weekday) == {("3",): 3, ("7",): 1}
    assert a.recency == (
        Recency("Tarjeta Crédito", 3, (1, 2, 2, 3), 0),
        Recency("Tarjeta Débito", 1, (0, 0, 0, 0), 0),
    )
    thirty = a.volume[0]
    assert (thirty.days, thirty.cards, thirty.per_card.rows) == (30, 2, 2)
    assert thirty.per_card.quantiles is not None
    assert thirty.per_card.quantiles[2] == 1.5
    assert groups(a.channels) == {
        ("ATM", "Tarjeta Crédito"): 1,
        ("POS", "Tarjeta Crédito"): 2,
        ("Web", "Tarjeta Crédito"): 1,
    }
    assert groups(a.abroad) == {("Colombia",): (1, 0), ("Mexico",): (3, 1)}
    assert groups(a.destinations) == {("USA",): 1}


def test_reads_decline_codes_by_status_and_over_time(
    card_staged: duckdb.DuckDBPyConnection,
) -> None:
    d = declines(card_staged)

    assert groups(d.codes_by_status) == {
        ("Approved", "00"): 2,
        ("Declined", "51"): 1,
        ("Declined", None): 1,
        ("Pending", "05"): 1,
    }
    assert groups(d.monthly) == {
        ("2025-01",): (1, 0),
        ("2026-03",): (1, 0),
        ("2026-06",): (3, 2),
    }
    assert groups(d.monthly_codes) == {("2026-06", "51"): 1, ("2026-06", None): 1}
    assert groups(d.rates[0]) == {
        ("ATM",): (1, 0),
        ("App",): (1, 0),
        ("POS",): (2, 1),
        ("Web",): (1, 1),
    }
    assert d.missing_code == (Window(30, 1, 1), Window(90, 1, 1), Window(365, 1, 1))
    assert [a.field for a in d.association][:3] == [
        "channel",
        "transaction_type",
        "merchant_category",
    ]
    assert d.association[0].rows == 2
    assert d.reference.field == "transaction_status"


def test_counts_fraud_marks_on_active_cards_per_window(
    card_staged: duckdb.DuckDBPyConnection,
) -> None:
    f = fraud(card_staged)

    assert groups(f.rates[0])[("POS",)] == (2, 1)
    assert groups(f.rates[-1]) == {
        ("Approved",): (2, 0),
        ("Declined",): (2, 1),
        ("Pending",): (1, 0),
    }
    assert f.windows == (Window(30, 1, 1), Window(90, 1, 1), Window(365, 1, 1))
    assert f.reference.field == "fraud_score"
    assert f.reference.field_values == 2


def test_cramers_v_reads_zero_when_independent_and_one_when_determined() -> None:
    independent = np.array([[500.0, 500.0], [500.0, 500.0]])
    determined = np.array([[1000.0, 0.0], [0.0, 1000.0]])

    assert cramers_v(independent) == 0.0
    assert cramers_v(determined) == pytest.approx(1.0, abs=1e-3)
    assert cramers_v(np.array([[10.0, 20.0], [0.0, 0.0]])) is None


def test_reads_balances_against_limits_and_accounts(
    card_staged: duckdb.DuckDBPyConnection,
) -> None:
    b = balances(card_staged)

    assert (
        b.credit.active_cards,
        b.credit.with_limit,
        b.credit.over_limit,
        b.credit.at_zero,
        b.credit.negative,
    ) == (3, 2, 1, 1, 0)
    assert b.credit.utilization.rows == 2
    assert groups(b.credit.utilization_bands) == {("010",): 1, ("over",): 1}
    assert groups(b.credit.missing_limit) == {("Active",): (3, 1)}
    assert (
        b.debit.active_cards,
        b.debit.with_limit,
        b.debit.at_zero,
        b.debit.matching_an_account,
    ) == (1, 0, 0, 1)
    assert [(c.product_type, c.currency, c.balances.rows) for c in b.by_currency] == [
        ("Tarjeta Crédito", "COP", 1),
        ("Tarjeta Crédito", "USD", 2),
        ("Tarjeta Débito", "ARS", 1),
    ]


def test_finds_the_date_conflicts(card_staged: duckdb.DuckDBPyConnection) -> None:
    d = dates(card_staged)

    assert d.cards == (
        CardDates("Active", 4, 0, 1, 0, 1),
        CardDates("Blocked", 1, 1, 0, 0, 0),
    )
    last = d.last_transaction
    assert last == LastTransaction(5, 1, 1, 1, 1, 1, 0, last.gap_days)
    assert last.gap_days.rows == 3
    assert d.transactions == (
        TransactionDates(None, 5, 1, 2),
        TransactionDates(365, 4, 1, 2),
        TransactionDates(90, 3, 1, 2),
        TransactionDates(30, 3, 1, 2),
    )


def test_reads_currencies_and_the_rate_behind_amount_usd(
    card_staged: duckdb.DuckDBPyConnection,
) -> None:
    c = currency(card_staged, BUSINESS_DATE)

    assert groups(c.cards) == {
        ("Argentina", "ARS"): 1,
        ("Colombia", "COP"): 1,
        ("Mexico", "USD"): 3,
    }
    assert groups(c.transactions) == {
        ("ARS", "ARS"): 1,
        ("COP", "COP"): 1,
        ("USD", "USD"): 3,
    }
    assert c.conversions == (
        Conversion("ARS", 1, 1, (350.0, 350.0, 350.0), (350.0, 350.0)),
        Conversion("COP", 1, 1, (4000.0, 4000.0, 4000.0), (3900.0, 4100.0)),
        Conversion("USD", 3, 0, None, None),
    )


def test_measures_fields_in_the_rows_that_should_carry_them(
    card_staged: duckdb.DuckDBPyConnection,
) -> None:
    m = missing(card_staged)
    fields = {f"{f.table}.{f.name}": (f.populated, f.rows) for f in m.fields}

    assert fields["products.credit_limit"] == (2, 3)
    assert fields["products.interest_rate"] == (3, 5)
    assert fields["products.expiration_date"] == (4, 5)
    assert fields["products.last_transaction_date"] == (4, 5)
    assert fields["transactions.response_code"] == (4, 5)
    assert fields["transactions.merchant_name"] == (2, 3)
    assert fields["transactions.transaction_category"] == (3, 4)
    assert fields["transactions.amount_usd"] == (2, 2)
    pairs = {(p.first, p.second): p for p in m.purchase_pairs}
    together = pairs["merchant_name", "merchant_category"]
    assert (together.rows, together.both_missing) == (3, 1)
    assert together.expected == pytest.approx(1 / 3)
    assert pairs["response_code", "merchant_name"].both_missing == 0
    assert len(m.credit_card_pairs) == 6


def test_dates_complaints_and_contacts_by_twelve_month_period(
    card_staged: duckdb.DuckDBPyConnection,
) -> None:
    c = context(card_staged)

    assert groups(c.complaints) == {
        ("1", "Fees"): (1, 0),
        ("1", "Transactions"): (1, 1),
        ("2", "Fees"): (1, 0),
    }
    assert groups(c.contacts) == {("1", "Producto"): 1, ("3", "Queja"): 1}


def test_runs_as_of_the_instant_it_is_given(card_bank: tuple[Lock, Path]) -> None:
    lock, root = card_bank
    result = card_support(lock, root, TABLES, BUSINESS_DATE, AS_OF, log=lambda _: None)

    assert isinstance(result, CardSupport)
    assert (result.customers, result.held_out) == (3, 1)
    assert result.activity.transactions == 5


def test_needs_the_tables_it_reads(card_bank: tuple[Lock, Path]) -> None:
    lock, root = card_bank
    with pytest.raises(AnalysisError, match="needs daily_exchange_rates"):
        card_support(lock, root, TABLES[:-1], BUSINESS_DATE, AS_OF, log=lambda _: None)


def test_a_day_ends_at_the_as_of_time(card_staged: duckdb.DuckDBPyConnection) -> None:
    assert one(
        card_staged,
        "select business_day(timestamp '2026-06-18 06:00:00'), "
        "business_day(timestamp '2026-06-17 06:00:01')",
    ) == (date(2026, 6, 17), date(2026, 6, 17))
