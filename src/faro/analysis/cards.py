"""
What the development customers' cards and card transactions hold at the as-of instant, for the card support
policy and the evaluation ADR. Held-out customers are set aside by ADR-0003's rule before anything is read.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

import duckdb
import numpy as np
import numpy.typing as npt
import polars as pl

from faro.analysis.candidates import (
    CARDS,
    DECLINE_REASONS,
    IS_ACCOUNT,
    IS_CARD,
    IS_PURCHASE,
    Scope,
    sql_list,
)
from faro.analysis.catalog import Table
from faro.analysis.selection import (
    STAGED,
    FieldPopulation,
    Log,
    field_populations,
    stage,
)
from faro.analysis.source import AnalysisError, connect, one, table_keys
from faro.dataset.lock import Lock
from faro.split import held_out

CREDIT, DEBIT = CARDS
# The ISO 8583 meanings ADR-0003's rule reads the decline codes with.
MEANINGS = dict(
    zip(
        DECLINE_REASONS,
        ("do not honor", "invalid card number", "insufficient funds", "expired card"),
        strict=True,
    )
)
WINDOWS = (30, 90, 365)
RECENCY = (7, 30, 90, 365)
VOLUME_WINDOWS = (30, 90)
YEAR = 365
# Customers holding more cards than this are counted together.
MOST_CARDS = 5
UTILIZATION_STEP = 5
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9, 0.99)
MISSING = "(missing)"
NEEDED = STAGED | {"daily_exchange_rates"}

# Fields an explanation or a rule might hinge on, as the card transactions carry them.
FIELDS = {
    "channel": "channel",
    "transaction_type": "transaction_type",
    "merchant_category": "merchant_category",
    "product_type": "product_type",
    "abroad": "abroad",
    "country": "country",
    "segment": "segment",
    "amount_decile": "amount_decile",
    "hour": "hour(transaction_date)",
    "weekday": "isodow(business_day(transaction_date))",
    "month": "strftime(business_day(transaction_date), '%Y-%m')",
}
RATE_FIELDS = (
    "channel",
    "transaction_type",
    "merchant_category",
    "product_type",
    "abroad",
    "country",
    "segment",
)
PURCHASE_GAPS = (
    "response_code",
    "merchant_name",
    "merchant_category",
    "transaction_category",
    "fraud_score",
)
CREDIT_CARD_GAPS = ("credit_limit", "interest_rate", "expiration_date", "days_past_due")

Floats = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Group:
    # The values of the breakdown's columns, in order; None where the field is missing.
    values: tuple[str | None, ...]
    rows: int
    # Rows among them that meet the breakdown's condition, when it has one.
    hits: int | None = None


@dataclass(frozen=True)
class Breakdown:
    by: tuple[str, ...]
    groups: tuple[Group, ...]

    @property
    def rows(self) -> int:
        return sum(g.rows for g in self.groups)


@dataclass(frozen=True)
class Spread:
    rows: int
    # At QUANTILES; None when no row has the measure.
    quantiles: tuple[float, ...] | None


@dataclass(frozen=True)
class Window:
    days: int
    transactions: int
    customers: int


@dataclass(frozen=True)
class Association:
    field: str
    rows: int
    # Distinct values on each side, a missing value counted as one.
    target_values: int
    field_values: int
    # Bias-corrected, so that independent fields read near zero at any sample size; None when a side has one
    # value or the sample is too small to correct.
    cramers_v: float | None


@dataclass(frozen=True)
class CardNumbers:
    cards: int
    distinct: int
    digits_only: int
    lengths: tuple[int, ...]
    leading_digits: Breakdown
    luhn_valid: int
    # Customers holding two or more cards that end in the same four digits.
    shared_last_four: int
    shared_last_four_active: int
    shared_last_four_active_same_type: int


@dataclass(frozen=True)
class Holders:
    customers: int
    status: Breakdown
    cards_per_customer: Breakdown
    active_per_customer: Breakdown
    by_country_segment: Breakdown
    holder_status: Breakdown
    numbers: CardNumbers


@dataclass(frozen=True)
class Recency:
    product_type: str
    active_cards: int
    # Active cards whose last transaction falls within each of RECENCY's windows.
    within: tuple[int, ...]
    without_transactions: int


@dataclass(frozen=True)
class Volume:
    days: int
    # Active cards with a transaction in the window, and how many each has.
    cards: int
    per_card: Spread


@dataclass(frozen=True)
class Activity:
    transactions: int
    daily: Breakdown
    weekday: Breakdown
    recency: tuple[Recency, ...]
    volume: tuple[Volume, ...]
    channels: Breakdown
    types: Breakdown
    abroad: Breakdown
    destinations: Breakdown


@dataclass(frozen=True)
class Declines:
    codes_by_status: Breakdown
    monthly: Breakdown
    monthly_codes: Breakdown
    rates: tuple[Breakdown, ...]
    association: tuple[Association, ...]
    # The code against transaction_status, which it does follow: what an association looks like on this scale.
    reference: Association
    missing_code: tuple[Window, ...]


@dataclass(frozen=True)
class Fraud:
    rates: tuple[Breakdown, ...]
    association: tuple[Association, ...]
    # is_fraud against fraud_score, which is drawn from it.
    reference: Association
    windows: tuple[Window, ...]


@dataclass(frozen=True)
class CreditBalances:
    active_cards: int
    with_limit: int
    over_limit: int
    at_zero: int
    negative: int
    utilization: Spread
    utilization_bands: Breakdown
    missing_limit: Breakdown


@dataclass(frozen=True)
class DebitBalances:
    active_cards: int
    with_limit: int
    at_zero: int
    negative: int
    # Active debit cards whose balance equals one of the holder's account balances.
    matching_an_account: int


@dataclass(frozen=True)
class CurrencyBalance:
    product_type: str
    currency: str
    balances: Spread


@dataclass(frozen=True)
class Balances:
    credit: CreditBalances
    debit: DebitBalances
    by_currency: tuple[CurrencyBalance, ...]


@dataclass(frozen=True)
class CardDates:
    product_status: str
    cards: int
    expiration_missing: int
    # Expiration before the business date.
    expired: int
    expires_before_opening: int
    updated_after_as_of: int


@dataclass(frozen=True)
class LastTransaction:
    cards: int
    equal: int
    # last_transaction_date before the card's last transaction, or after it.
    earlier: int
    later: int
    recorded_without_transactions: int
    transactions_without_record: int
    neither: int
    # Days between last_transaction_date and the card's last transaction, where both exist.
    gap_days: Spread


@dataclass(frozen=True)
class TransactionDates:
    # None for the whole history.
    days: int | None
    transactions: int
    before_opening: int
    after_expiration: int


@dataclass(frozen=True)
class Dates:
    cards: tuple[CardDates, ...]
    last_transaction: LastTransaction
    transactions: tuple[TransactionDates, ...]


@dataclass(frozen=True)
class Conversion:
    currency: str
    transactions: int
    converted: int
    # amount / amount_usd at the 1st, 50th, and 99th percentiles.
    ratio: tuple[float, float, float] | None
    # The lowest and highest daily rate from USD in the bank's exchange rates.
    daily_rate: tuple[float, float] | None


@dataclass(frozen=True)
class Currency:
    cards: Breakdown
    transactions: Breakdown
    conversions: tuple[Conversion, ...]


@dataclass(frozen=True)
class CoMissing:
    first: str
    second: str
    rows: int
    first_missing: int
    second_missing: int
    both_missing: int

    @property
    def expected(self) -> float:
        return (
            self.first_missing * self.second_missing / self.rows if self.rows else 0.0
        )


@dataclass(frozen=True)
class Missing:
    fields: tuple[FieldPopulation, ...]
    # Whether one gap makes another likelier, among card purchases and among credit cards.
    purchase_pairs: tuple[CoMissing, ...]
    credit_card_pairs: tuple[CoMissing, ...]


@dataclass(frozen=True)
class Context:
    # Complaints by development customers; hits are those whose affected product is one of their cards.
    complaints: Breakdown
    contacts: Breakdown


@dataclass(frozen=True)
class CardSupport:
    snapshot_id: str
    duckdb_version: str
    business_date: date
    as_of: datetime
    customers: int
    held_out: int
    holders: Holders
    activity: Activity
    declines: Declines
    fraud: Fraud
    balances: Balances
    dates: Dates
    currency: Currency
    missing: Missing
    context: Context


def breakdown(
    con: duckdb.DuckDBPyConnection,
    source: str,
    by: Mapping[str, str],
    where: str = "true",
    hit: str | None = None,
    rows: str = "count(*)",
) -> Breakdown:
    columns = ", ".join(f"cast({expr} as varchar)" for expr in by.values())
    hits = f", count(*) filter (where {hit})" if hit else ""
    result = con.execute(
        f"select {columns}, {rows}{hits} from {source} where {where} "
        "group by all order by all"
    ).fetchall()
    width = len(by)
    return Breakdown(
        by=tuple(by),
        groups=tuple(
            Group(tuple(r[:width]), r[width], r[width + 1] if hit else None)
            for r in result
        ),
    )


def spread(con: duckdb.DuckDBPyConnection, measure: str, source: str) -> Spread:
    n, quantiles = one(
        con,
        f"select count({measure}), quantile_cont({measure}, {list(QUANTILES)}) from {source}",
    )
    return Spread(n, None if quantiles is None else tuple(float(q) for q in quantiles))


def cramers_v(counts: Floats) -> float | None:
    counts = counts[counts.sum(axis=1) > 0][:, counts.sum(axis=0) > 0]
    n = counts.sum()
    r, k = counts.shape
    if r < 2 or k < 2 or n < 2:
        return None
    expected = np.outer(counts.sum(axis=1), counts.sum(axis=0)) / n
    chi2 = float(((counts - expected) ** 2 / expected).sum())
    # Bergsma (2013): without it, V over many categories reads above zero on independent fields.
    phi2 = max(0.0, chi2 / n - (k - 1) * (r - 1) / (n - 1))
    rows = r - (r - 1) ** 2 / (n - 1)
    columns = k - (k - 1) ** 2 / (n - 1)
    smaller = min(rows, columns) - 1
    return float(np.sqrt(phi2 / smaller)) if smaller > 0 else None


def associate(
    con: duckdb.DuckDBPyConnection,
    source: str,
    target: str,
    field: str,
    expr: str,
    where: str = "true",
) -> Association:
    cells = con.execute(
        f"select coalesce(cast({target} as varchar), '{MISSING}'), "
        f"coalesce(cast({expr} as varchar), '{MISSING}'), count(*) "
        f"from {source} where {where} group by all"
    ).fetchall()
    targets = sorted({t for t, _, _ in cells})
    values = sorted({v for _, v, _ in cells})
    counts = np.zeros((len(targets), len(values)))
    for t, v, n in cells:
        counts[targets.index(t), values.index(v)] = n
    return Association(
        field=field,
        rows=int(counts.sum()),
        target_values=len(targets),
        field_values=len(values),
        cramers_v=cramers_v(counts),
    )


def split(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    ids = [
        r[0]
        for r in con.execute("select customer_id from customers order by 1").fetchall()
    ]
    frame = pl.DataFrame(
        {"customer_id": ids, "held_out": [held_out(i) for i in ids]},
        schema={"customer_id": pl.String, "held_out": pl.Boolean},
    )
    con.register("split_frame", frame)
    con.execute(
        "create or replace temp table development as "
        "select customer_id from split_frame where not held_out"
    )
    con.unregister("split_frame")
    development: int = one(con, "select count(*) from development")[0]
    return development, len(ids) - development


def define_days(con: duckdb.DuckDBPyConnection, business_date: date) -> None:
    con.execute(f"set variable business_date = date '{business_date.isoformat()}'")
    # A day is the 24 hours ending at the as-of time of day, as the windows count them.
    con.execute(
        "create or replace temp macro business_day(ts) as cast(ts - (getvariable('as_of') "
        "- date_trunc('day', getvariable('as_of'))) - interval 1 microsecond as date)"
    )


def prepare(con: duckdb.DuckDBPyConnection, business_date: date) -> None:
    define_days(con, business_date)
    con.execute(
        f"""
        create or replace temp table cards as
        select p.product_id, p.customer_id, p.product_type, p.product_number, p.product_status,
          p.currency, p.current_balance, p.credit_limit, p.interest_rate, p.expiration_date,
          p.days_past_due, p.opening_date, p.last_transaction_date, p.last_updated,
          c.country, c.segment, c.customer_status
        from products p join development d on d.customer_id = p.customer_id
          join customers c on c.customer_id = p.customer_id
        where p.{IS_CARD}
        """
    )
    con.execute(
        """
        create or replace temp table card_transactions as
        select t.transaction_id, t.customer_id, t.product_id, t.product_type, t.transaction_date,
          t.transaction_type, t.transaction_category, t.amount, t.amount_usd, t.currency,
          t.channel, t.merchant_name, t.merchant_category, t.transaction_country,
          t.transaction_status, t.response_code, t.is_fraud, t.fraud_score,
          k.product_status, k.opening_date, k.expiration_date, k.currency as card_currency,
          k.country, k.segment, t.transaction_country <> k.country as abroad,
          ntile(10) over (partition by t.currency order by t.amount, t.transaction_id)
            as amount_decile
        from transactions t join cards k on k.product_id = t.product_id
        """
    )


def _capped(n: str) -> str:
    return f"least({n}, {MOST_CARDS})"


def card_numbers(con: duckdb.DuckDBPyConnection) -> CardNumbers:
    digits = "regexp_full_match(product_number, '[0-9]+')"
    luhn = (
        "list_sum(list_transform(list_reverse(string_split(product_number, '')), lambda d, i: "
        "case when i % 2 = 0 then 2 * try_cast(d as integer) - "
        "case when try_cast(d as integer) > 4 then 9 else 0 end else try_cast(d as integer) end)) "
        "% 10 = 0"
    )

    def shared(where: str, *by: str) -> int:
        n: int = one(
            con,
            "select count(distinct customer_id) from (select customer_id from cards "
            f"where {where} group by customer_id, right(product_number, 4){''.join(', ' + b for b in by)} "
            "having count(*) > 1)",
        )[0]
        return n

    cards, distinct, digits_only, luhn_valid, lengths = one(
        con,
        f"select count(*), count(distinct product_number), count(*) filter (where {digits}), "
        f"count(*) filter (where {digits} and {luhn}), "
        "list_sort(list_distinct(list(length(product_number)))) from cards",
    )
    active = "product_status = 'Active'"
    return CardNumbers(
        cards=cards,
        distinct=distinct,
        digits_only=digits_only,
        lengths=tuple(lengths or ()),
        leading_digits=breakdown(
            con, "cards", {"leading_digit": "left(product_number, 1)"}
        ),
        luhn_valid=luhn_valid,
        shared_last_four=shared("true"),
        shared_last_four_active=shared(active),
        shared_last_four_active_same_type=shared(active, "product_type"),
    )


def holders(con: duckdb.DuckDBPyConnection) -> Holders:
    per_customer = "(select customer_id, count(*) as cards from cards group by 1)"
    active_per_customer = (
        "(select customer_id, product_type, count(*) as cards from cards "
        "where product_status = 'Active' group by 1, 2)"
    )
    return Holders(
        customers=one(con, "select count(distinct customer_id) from cards")[0],
        status=breakdown(
            con,
            "cards",
            {"product_type": "product_type", "product_status": "product_status"},
        ),
        cards_per_customer=breakdown(con, per_customer, {"cards": _capped("cards")}),
        active_per_customer=breakdown(
            con,
            active_per_customer,
            {"product_type": "product_type", "active_cards": _capped("cards")},
        ),
        by_country_segment=breakdown(
            con,
            "cards",
            {"country": "country", "segment": "segment"},
            where="product_status = 'Active'",
            rows="count(distinct customer_id)",
        ),
        holder_status=breakdown(
            con,
            "cards",
            {"customer_status": "customer_status"},
            where="product_status = 'Active'",
        ),
        numbers=card_numbers(con),
    )


def recency(con: duckdb.DuckDBPyConnection) -> tuple[Recency, ...]:
    within = ", ".join(
        f"count(*) filter (where within(last, {days}))" for days in RECENCY
    )
    rows = con.execute(
        f"""
        select k.product_type, count(*), {within}, count(*) filter (where last is null)
        from cards k left join (
          select product_id, max(transaction_date) as last from card_transactions group by 1
        ) t on t.product_id = k.product_id
        where k.product_status = 'Active'
        group by 1 order by 1
        """
    ).fetchall()
    return tuple(
        Recency(r[0], r[1], tuple(r[2 : 2 + len(RECENCY)]), r[-1]) for r in rows
    )


def volume(con: duckdb.DuckDBPyConnection, days: int) -> Volume:
    source = (
        "(select product_id, count(*) as n from card_transactions "
        f"where product_status = 'Active' and within(transaction_date, {days}) group by 1)"
    )
    return Volume(
        days=days,
        cards=one(con, f"select count(*) from {source}")[0],
        per_card=spread(con, "n", source),
    )


def activity(con: duckdb.DuckDBPyConnection) -> Activity:
    year = f"within(transaction_date, {YEAR})"
    return Activity(
        transactions=one(con, "select count(*) from card_transactions")[0],
        daily=breakdown(
            con, "card_transactions", {"day": "business_day(transaction_date)"}
        ),
        weekday=breakdown(
            con,
            "card_transactions",
            {"weekday": FIELDS["weekday"]},
            where=year,
        ),
        recency=recency(con),
        volume=tuple(volume(con, days) for days in VOLUME_WINDOWS),
        channels=breakdown(
            con,
            "card_transactions",
            {"channel": "channel", "product_type": "product_type"},
            where=year,
        ),
        types=breakdown(
            con,
            "card_transactions",
            {"transaction_type": "transaction_type", "product_type": "product_type"},
            where=year,
        ),
        abroad=breakdown(
            con, "card_transactions", {"country": "country"}, where=year, hit="abroad"
        ),
        destinations=breakdown(
            con,
            "card_transactions",
            {"transaction_country": "transaction_country"},
            where=f"{year} and abroad",
        ),
    )


def _windows(con: duckdb.DuckDBPyConnection, where: str) -> tuple[Window, ...]:
    return tuple(
        Window(
            days,
            *one(
                con,
                "select count(*), count(distinct customer_id) from card_transactions "
                f"where {where} and within(transaction_date, {days})",
            ),
        )
        for days in WINDOWS
    )


def _associations(
    con: duckdb.DuckDBPyConnection, target: str, where: str = "true"
) -> tuple[Association, ...]:
    return tuple(
        associate(con, "card_transactions", target, field, expr, where)
        for field, expr in FIELDS.items()
    )


def declines(con: duckdb.DuckDBPyConnection) -> Declines:
    declined = "transaction_status = 'Declined'"
    month = {"month": FIELDS["month"]}
    return Declines(
        codes_by_status=breakdown(
            con,
            "card_transactions",
            {
                "transaction_status": "transaction_status",
                "response_code": "response_code",
            },
        ),
        monthly=breakdown(con, "card_transactions", month, hit=declined),
        monthly_codes=breakdown(
            con,
            "card_transactions",
            {**month, "response_code": "response_code"},
            where=declined,
        ),
        rates=tuple(
            breakdown(con, "card_transactions", {f: FIELDS[f]}, hit=declined)
            for f in RATE_FIELDS
        ),
        association=_associations(con, "response_code", declined),
        reference=associate(
            con,
            "card_transactions",
            "response_code",
            "transaction_status",
            "transaction_status",
        ),
        missing_code=_windows(
            con,
            f"{declined} and (response_code is null "
            f"or response_code not in {sql_list(DECLINE_REASONS)})",
        ),
    )


def fraud(con: duckdb.DuckDBPyConnection) -> Fraud:
    return Fraud(
        rates=tuple(
            breakdown(con, "card_transactions", {f: FIELDS.get(f, f)}, hit="is_fraud")
            for f in (*RATE_FIELDS, "transaction_status")
        ),
        association=_associations(con, "is_fraud"),
        reference=associate(
            con,
            "card_transactions",
            "is_fraud",
            "fraud_score",
            "floor(fraud_score / 10) * 10",
        ),
        windows=_windows(con, "is_fraud and product_status = 'Active'"),
    )


def balances(con: duckdb.DuckDBPyConnection) -> Balances:
    active_credit = f"product_status = 'Active' and product_type = '{CREDIT}'"
    active_debit = f"product_status = 'Active' and product_type = '{DEBIT}'"
    utilization = "current_balance / credit_limit"
    # Labeled by the band's lower bound in percent, padded so that the bands sort in order.
    band = (
        f"case when current_balance > credit_limit then 'over' else lpad(cast(cast(least("
        f"floor(100 * {utilization} / {UTILIZATION_STEP}) * {UTILIZATION_STEP}, "
        f"100 - {UTILIZATION_STEP}) as integer) as varchar), 3, '0') end"
    )
    counts = (
        "select count(*), count(credit_limit), "
        "count(*) filter (where current_balance > credit_limit), "
        "count(*) filter (where current_balance = 0), "
        "count(*) filter (where current_balance < 0) from cards where "
    )
    credit = one(con, counts + active_credit)
    debit = one(con, counts + active_debit)
    matching: int = one(
        con,
        f"""
        select count(*) from cards k where {active_debit} and exists (
          select 1 from products a where a.customer_id = k.customer_id and a.{IS_ACCOUNT}
            and a.current_balance = k.current_balance)
        """,
    )[0]
    return Balances(
        credit=CreditBalances(
            active_cards=credit[0],
            with_limit=credit[1],
            over_limit=credit[2],
            at_zero=credit[3],
            negative=credit[4],
            utilization=spread(
                con,
                utilization,
                f"(select * from cards where {active_credit} and credit_limit > 0)",
            ),
            utilization_bands=breakdown(
                con,
                "cards",
                {"band": band},
                where=f"{active_credit} and credit_limit > 0",
            ),
            missing_limit=breakdown(
                con,
                "cards",
                {"product_status": "product_status"},
                where=f"product_type = '{CREDIT}'",
                hit="credit_limit is null",
            ),
        ),
        debit=DebitBalances(
            active_cards=debit[0],
            with_limit=debit[1],
            at_zero=debit[3],
            negative=debit[4],
            matching_an_account=matching,
        ),
        by_currency=tuple(
            CurrencyBalance(
                product_type,
                currency,
                spread(
                    con,
                    "current_balance",
                    f"(select * from cards where product_status = 'Active' "
                    f"and product_type = '{product_type}' and currency = '{currency}')",
                ),
            )
            for product_type, currency in con.execute(
                "select distinct product_type, currency from cards "
                "where product_status = 'Active' order by all"
            ).fetchall()
        ),
    )


def dates(con: duckdb.DuckDBPyConnection) -> Dates:
    card_rows = con.execute(
        """
        select product_status, count(*), count(*) filter (where expiration_date is null),
          count(*) filter (where expiration_date < getvariable('business_date')),
          count(*) filter (where expiration_date < opening_date),
          count(*) filter (where last_updated > getvariable('as_of'))
        from cards group by 1 order by 1
        """
    ).fetchall()
    last = """
        (select k.last_transaction_date as recorded, t.last as actual from cards k left join (
          select product_id, max(transaction_date) as last from card_transactions group by 1
        ) t on t.product_id = k.product_id)
    """
    cards, equal, earlier, later, unused, unrecorded, neither = one(
        con,
        f"""
        select count(*), count(*) filter (where recorded = actual),
          count(*) filter (where recorded < actual), count(*) filter (where recorded > actual),
          count(*) filter (where recorded is not null and actual is null),
          count(*) filter (where recorded is null and actual is not null),
          count(*) filter (where recorded is null and actual is null)
        from {last}
        """,
    )
    return Dates(
        cards=tuple(CardDates(*r) for r in card_rows),
        last_transaction=LastTransaction(
            cards=cards,
            equal=equal,
            earlier=earlier,
            later=later,
            recorded_without_transactions=unused,
            transactions_without_record=unrecorded,
            neither=neither,
            gap_days=spread(con, "abs(epoch(recorded) - epoch(actual)) / 86400", last),
        ),
        transactions=tuple(
            TransactionDates(
                days,
                *one(
                    con,
                    "select count(*), "
                    "count(*) filter (where cast(transaction_date as date) < opening_date), "
                    "count(*) filter (where cast(transaction_date as date) > expiration_date) "
                    "from card_transactions where "
                    + ("true" if days is None else f"within(transaction_date, {days})"),
                ),
            )
            for days in (None, *reversed(WINDOWS))
        ),
    )


def currency(con: duckdb.DuckDBPyConnection, business_date: date) -> Currency:
    rows = con.execute(
        """
        select currency, count(*), count(amount_usd),
          quantile_cont(amount / amount_usd, [0.01, 0.5, 0.99])
        from card_transactions group by 1 order by 1
        """
    ).fetchall()
    rates = {
        target: (float(low), float(high))
        for target, low, high in con.execute(
            "select target_currency, min(try_cast(exchange_rate as double)), "
            "max(try_cast(exchange_rate as double)) from raw_daily_exchange_rates "
            "where source_currency = 'USD' and try_cast(date as date) <= ? group by 1",
            [business_date],
        ).fetchall()
    }
    return Currency(
        cards=breakdown(con, "cards", {"country": "country", "currency": "currency"}),
        transactions=breakdown(
            con,
            "card_transactions",
            {"card_currency": "card_currency", "currency": "currency"},
        ),
        conversions=tuple(
            Conversion(
                currency=c,
                transactions=n,
                converted=converted,
                ratio=None
                if ratio is None
                else (float(ratio[0]), float(ratio[1]), float(ratio[2])),
                daily_rate=rates.get(c),
            )
            for c, n, converted, ratio in rows
        ),
    )


def co_missing(
    con: duckdb.DuckDBPyConnection, table: str, rows: str, fields: Sequence[str]
) -> tuple[CoMissing, ...]:
    pairs = [(a, b) for i, a in enumerate(fields) for b in fields[i + 1 :]]
    aggregates = ", ".join(
        f"count(*) filter (where {a} is null and {b} is null)" for a, b in pairs
    )
    nulls = ", ".join(f"count(*) filter (where {f} is null)" for f in fields)
    result = one(
        con, f"select count(*), {nulls}, {aggregates} from {table} where {rows}"
    )
    n, missing, both = result[0], result[1 : 1 + len(fields)], result[1 + len(fields) :]
    by_field = dict(zip(fields, missing, strict=True))
    return tuple(
        CoMissing(a, b, n, by_field[a], by_field[b], k)
        for (a, b), k in zip(pairs, both, strict=True)
    )


def missing(con: duckdb.DuckDBPyConnection) -> Missing:
    card_scope = Scope(
        "cards",
        "true",
        (
            "product_number",
            "product_status",
            "currency",
            "current_balance",
            "opening_date",
            "credit_limit",
            "interest_rate",
            "expiration_date",
            "days_past_due",
            "last_transaction_date",
        ),
        {
            "credit_limit": f"product_type = '{CREDIT}'",
            "days_past_due": f"product_type = '{CREDIT}'",
        },
    )
    transaction_scope = Scope(
        "card_transactions",
        "true",
        (
            "transaction_type",
            "transaction_status",
            "response_code",
            "is_fraud",
            "fraud_score",
            "channel",
            "transaction_country",
            "merchant_name",
            "merchant_category",
            "transaction_category",
            "amount_usd",
        ),
        {
            "merchant_name": IS_PURCHASE,
            "merchant_category": IS_PURCHASE,
            "transaction_category": "transaction_type <> 'Withdrawal'",
            "amount_usd": "currency <> 'USD'",
        },
    )
    return Missing(
        fields=tuple(
            replace(f, table="products") for f in field_populations(con, card_scope)
        )
        + tuple(
            replace(f, table="transactions")
            for f in field_populations(con, transaction_scope)
        ),
        purchase_pairs=co_missing(con, "card_transactions", IS_PURCHASE, PURCHASE_GAPS),
        credit_card_pairs=co_missing(
            con, "cards", f"product_type = '{CREDIT}'", CREDIT_CARD_GAPS
        ),
    )


def _period(column: str) -> str:
    return (
        f"case when within({column}, {YEAR}) then '1' "
        f"when within({column}, {2 * YEAR}) then '2' else '3' end"
    )


def context(con: duckdb.DuckDBPyConnection) -> Context:
    return Context(
        complaints=breakdown(
            con,
            "complaints k join development d on d.customer_id = k.customer_id",
            {"period": _period("creation_date"), "category": "category"},
            hit="affected_product_id in (select product_id from cards)",
        ),
        contacts=breakdown(
            con,
            "interactions i join development d on d.customer_id = i.customer_id",
            {"period": _period("interaction_date"), "reason": "reason_category"},
        ),
    )


def card_support(
    lock: Lock,
    root: Path,
    tables: Sequence[Table],
    business_date: date,
    as_of: datetime,
    log: Log = print,
) -> CardSupport:
    catalog = {t.name: t for t in tables}
    missing_tables = sorted(NEEDED - catalog.keys())
    if missing_tables:
        raise AnalysisError(
            f"the card support analysis needs {', '.join(missing_tables)}"
        )
    raw = [catalog[name] for name in sorted(NEEDED)]
    con = connect(root, raw, {t.name: table_keys(lock, t) for t in raw})
    try:
        log(f"Staging the tables as of {as_of}")
        stage(con, as_of)
        log("Setting the held-out customers aside")
        development, set_aside = split(con)
        prepare(con, business_date)
        log("Profiling the development customers' cards")
        result = CardSupport(
            snapshot_id=lock.snapshot_id,
            duckdb_version=duckdb.__version__,
            business_date=business_date,
            as_of=as_of,
            customers=development,
            held_out=set_aside,
            holders=holders(con),
            activity=activity(con),
            declines=declines(con),
            fraud=fraud(con),
            balances=balances(con),
            dates=dates(con),
            currency=currency(con, business_date),
            missing=missing(con),
            context=context(con),
        )
    finally:
        con.close()
    return result
