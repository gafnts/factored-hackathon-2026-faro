"""
Gate E2 of ADR-0003: whether a label the bank recorded carries signal for customers the model never saw.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import duckdb
import numpy as np
import numpy.typing as npt
import polars as pl
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from faro.analysis.candidates import Label
from faro.analysis.source import one
from faro.split import held_out

CHANCE = 0.5
PENALTY_C = 1.0
RESAMPLES = 1_000
LEVEL = 0.95
SEED = 20260927
MISSING = "(missing)"
SCORE_BAND = 10
DAYS_PER_YEAR = 365.25
DAYS_PER_MONTH = DAYS_PER_YEAR / 12

Floats = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Side:
    rows: int
    customers: int
    positives: int


@dataclass(frozen=True)
class Auc:
    estimate: float
    low: float
    high: float


@dataclass(frozen=True)
class Signal:
    label: str
    training: Side
    held_out: Side
    auc: Auc | None
    # fraud_score, drawn from the label, on the held-out rows where it is populated: context, outside the gate.
    fraud_score: Auc | None
    fraud_score_rows: int

    @property
    def passes(self) -> bool:
        return self.auc is not None and self.auc.low > CHANCE


@dataclass(frozen=True)
class ScoreBand:
    # None for unscored transactions.
    low: int | None
    transactions: int
    fraud: int


@dataclass(frozen=True)
class Features:
    derive: Callable[[pl.DataFrame, date], pl.DataFrame]
    categorical: tuple[str, ...]
    numeric: tuple[str, ...]


def _transaction_features(rows: pl.DataFrame, business_date: date) -> pl.DataFrame:
    return rows.select(
        "transaction_type",
        "transaction_category",
        "channel",
        "merchant_category",
        "currency",
        "product_type",
        (pl.col("transaction_country") != pl.col("customer_country"))
        .cast(pl.String)
        .alias("abroad"),
        pl.col("transaction_date").dt.hour().cast(pl.String).alias("hour"),
        pl.col("transaction_date").dt.weekday().cast(pl.String).alias("weekday"),
        pl.col("amount").cast(pl.Float64).log1p().alias("log_amount"),
    )


def _months_to(business_date: date, column: pl.Expr) -> pl.Expr:
    return (pl.lit(business_date) - column).dt.total_days() / DAYS_PER_MONTH


def _credit_features(rows: pl.DataFrame, business_date: date) -> pl.DataFrame:
    return rows.select(
        "segment",
        "country",
        "product_type",
        pl.col("credit_score").cast(pl.Float64),
        pl.col("estimated_monthly_income").cast(pl.Float64).log1p().alias("log_income"),
        (_months_to(business_date, pl.col("date_of_birth")) / 12).alias("age_years"),
        _months_to(business_date, pl.col("registration_date").dt.date()).alias(
            "months_registered"
        ),
        pl.col("interest_rate").cast(pl.Float64),
        pl.col("credit_limit").cast(pl.Float64).log1p().alias("log_credit_limit"),
        _months_to(business_date, pl.col("opening_date")).alias("months_open"),
    )


FEATURES = {
    "transaction": Features(
        _transaction_features,
        (
            "transaction_type",
            "transaction_category",
            "channel",
            "merchant_category",
            "currency",
            "product_type",
            "abroad",
            "hour",
            "weekday",
        ),
        ("log_amount",),
    ),
    "credit": Features(
        _credit_features,
        ("segment", "country", "product_type"),
        (
            "credit_score",
            "log_income",
            "age_years",
            "months_registered",
            "interest_rate",
            "log_credit_limit",
            "months_open",
        ),
    ),
}


def fraud_by_score(con: duckdb.DuckDBPyConnection, rows: str) -> tuple[ScoreBand, ...]:
    return tuple(
        ScoreBand(None if low is None else int(low), n, fraud)
        for low, n, fraud in con.execute(
            f"select floor(fraud_score / {SCORE_BAND}) * {SCORE_BAND} as low, count(*), "
            f"count(*) filter (where is_fraud) from transactions where {rows} "
            "group by 1 order by 1 nulls last"
        ).fetchall()
    )


def top_legitimate_score(con: duckdb.DuckDBPyConnection, rows: str) -> float | None:
    top = one(
        con, f"select max(fraud_score) from transactions where {rows} and not is_fraud"
    )[0]
    return None if top is None else float(top)


def model(features: Features) -> Pipeline:
    return make_pipeline(
        ColumnTransformer(
            [
                (
                    "categorical",
                    make_pipeline(
                        SimpleImputer(
                            strategy="constant",
                            fill_value=MISSING,
                            keep_empty_features=True,
                        ),
                        OneHotEncoder(handle_unknown="ignore"),
                    ),
                    list(features.categorical),
                ),
                (
                    "numeric",
                    make_pipeline(
                        SimpleImputer(strategy="median", add_indicator=True),
                        StandardScaler(),
                    ),
                    list(features.numeric),
                ),
            ]
        ),
        LogisticRegression(C=PENALTY_C, max_iter=1_000),
    )


def roc_auc(
    scores: Floats, labels: npt.NDArray[np.bool_], customers: Sequence[str]
) -> Auc | None:
    if labels.all() or not labels.any():
        return None
    # Ranks with ties shared, so each resample only reweighs rows by how often their customer was drawn.
    _, tie = np.unique(scores, return_inverse=True)
    _, customer = np.unique(np.asarray(customers), return_inverse=True)
    positive = labels.astype(np.float64)

    def weighted(weights: Floats) -> float:
        pos = np.bincount(tie, weights=weights * positive)
        neg = np.bincount(tie, weights=weights * (1 - positive))
        pairs = pos.sum() * neg.sum()
        if not pairs:
            return float("nan")
        return float((pos * (np.cumsum(neg) - neg / 2)).sum() / pairs)

    rng = np.random.default_rng(SEED)
    n = int(customer.max()) + 1
    draws = [
        weighted(
            np.bincount(rng.integers(0, n, n), minlength=n)[customer].astype(np.float64)
        )
        for _ in range(RESAMPLES)
    ]
    low, high = np.nanquantile(draws, [(1 - LEVEL) / 2, (1 + LEVEL) / 2])
    return Auc(weighted(np.ones(len(scores))), float(low), float(high))


def _side(rows: pl.DataFrame) -> Side:
    return Side(
        rows=rows.height,
        customers=rows.get_column("customer_id").n_unique(),
        positives=int(rows.get_column("label").sum()),
    )


def measure(
    con: duckdb.DuckDBPyConnection, label: Label, business_date: date
) -> Signal:
    features = FEATURES[label.kind]
    rows = con.execute(label.rows).pl().filter(pl.col("label").is_not_null())
    customers = rows.get_column("customer_id").unique().sort().to_list()
    split = rows.with_columns(
        pl.col("customer_id")
        .is_in([c for c in customers if held_out(c)])
        .alias("held_out")
    )
    training = split.filter(~pl.col("held_out"))
    testing = split.filter(pl.col("held_out"))
    labels = testing.get_column("label").to_numpy()

    auc = None
    if training.get_column("label").n_unique() == 2 and testing.height:
        fitted = model(features).fit(
            features.derive(training, business_date),
            training.get_column("label").to_numpy(),
        )
        scores = fitted.predict_proba(features.derive(testing, business_date))[:, 1]
        auc = roc_auc(scores, labels, testing.get_column("customer_id").to_list())

    fraud_score = None
    scored = 0
    if "fraud_score" in rows.columns:
        bank = testing.filter(pl.col("fraud_score").is_not_null())
        scored = bank.height
        fraud_score = roc_auc(
            bank.get_column("fraud_score").cast(pl.Float64).to_numpy(),
            bank.get_column("label").to_numpy(),
            bank.get_column("customer_id").to_list(),
        )
    return Signal(
        label=label.description,
        training=_side(training),
        held_out=_side(testing),
        auc=auc,
        fraud_score=fraud_score,
        fraud_score_rows=scored,
    )
