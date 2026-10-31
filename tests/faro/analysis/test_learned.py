"""
E2 finds signal only where the label has some, on customers the model never saw (DML-07 to DML-10).
"""

from collections.abc import Iterator
from datetime import date

import duckdb
import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from faro.analysis.candidates import Label
from faro.analysis.learned import (
    FEATURES,
    measure,
    roc_auc,
)

BUSINESS_DATE = date(2026, 6, 17)

# 60 customers, 10 transactions each; the channel alone gives the label away when it is ATM.
_TRANSACTIONS = """
    select 'C' || (i % 60) as customer_id, {label} as label,
      (i % 7 = 0)::int * 90.0 as fraud_score,
      'Purchase' as transaction_type, null as transaction_category,
      case when i % 7 = 0 then 'ATM' else 'POS' end as channel,
      null as merchant_category, 'MXN' as currency, 10.0 + i as amount,
      case when i % 3 = 0 then 'Brazil' else 'Mexico' end as transaction_country,
      'Mexico' as customer_country,
      timestamp '2026-06-01 00:00:00' + to_hours(i) as transaction_date,
      'Tarjeta Crédito' as product_type
    from range(600) r(i) order by i
"""


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    yield con
    con.close()


def test_the_estimate_matches_the_usual_roc_auc_with_ties() -> None:
    scores = np.array([0.1, 0.4, 0.4, 0.8, 0.8, 0.9, 0.2, 0.4])
    labels = np.array([False, False, True, True, False, True, False, True])
    customers = ["A", "A", "B", "B", "C", "C", "D", "D"]

    auc = roc_auc(scores, labels, customers)

    assert auc is not None
    assert auc.estimate == pytest.approx(roc_auc_score(labels, scores))
    assert auc.low <= auc.estimate <= auc.high
    assert roc_auc(scores, labels, customers) == auc


def test_has_no_estimate_without_both_classes() -> None:
    assert roc_auc(np.array([0.2, 0.7]), np.array([True, True]), ["A", "B"]) is None


def test_derives_the_transaction_features(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(_TRANSACTIONS.format(label="false")).pl()
    derived = FEATURES["transaction"].derive(rows, BUSINESS_DATE)

    assert derived.columns == [
        *FEATURES["transaction"].categorical[:6],
        "abroad",
        "hour",
        "weekday",
        "log_amount",
    ]
    assert derived.row(0, named=True)["abroad"] == "true"
    assert derived.row(1, named=True)["abroad"] == "false"
    assert derived.row(1, named=True)["hour"] == "1"


def test_derives_the_credit_features(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        "select 'Plus' as segment, 'Mexico' as country, 'Préstamo Personal' as product_type, "
        "700 as credit_score, 999.0 as estimated_monthly_income, date '1996-06-17' as date_of_birth, "
        "timestamp '2025-06-17 10:00:00' as registration_date, 12.5 as interest_rate, "
        "null::decimal(15,2) as credit_limit, date '2026-05-17' as opening_date"
    ).pl()
    derived = FEATURES["credit"].derive(rows, BUSINESS_DATE).row(0, named=True)

    assert derived["age_years"] == pytest.approx(30, abs=0.01)
    assert derived["months_registered"] == pytest.approx(12, abs=0.01)
    assert derived["months_open"] == pytest.approx(1, abs=0.05)
    assert derived["log_income"] == pytest.approx(np.log(1000))
    assert derived["log_credit_limit"] is None


def test_finds_a_signal_the_features_carry(con: duckdb.DuckDBPyConnection) -> None:
    label = Label("test", "transaction", _TRANSACTIONS.format(label="i % 7 = 0"))
    signal = measure(con, label, BUSINESS_DATE)

    assert signal.auc is not None
    assert signal.auc.low > 0.99
    assert signal.passes
    assert signal.training.customers + signal.held_out.customers == 60
    assert signal.training.rows + signal.held_out.rows == 600
    assert signal.fraud_score is not None
    assert signal.fraud_score_rows == signal.held_out.rows


def test_finds_none_in_a_label_the_features_dont_carry(
    con: duckdb.DuckDBPyConnection,
) -> None:
    label = Label("test", "transaction", _TRANSACTIONS.format(label="i % 60 < 30"))
    signal = measure(con, label, BUSINESS_DATE)

    assert signal.auc is not None
    assert signal.auc.low <= 0.5
    assert not signal.passes


def test_fits_nothing_when_training_holds_one_class(
    con: duckdb.DuckDBPyConnection,
) -> None:
    label = Label("test", "transaction", _TRANSACTIONS.format(label="false"))
    signal = measure(con, label, BUSINESS_DATE)

    assert signal.auc is None
    assert not signal.passes
