"""
The tiny export (ADR-0006's amendment of 2026-09-29): what gold would hold for the development personas, built from the
pinned snapshot, so the tools have data and the import is proved before the pipeline exists. The pipeline's first export
replaces it. Gold's rules, in SQL here until the pipeline's models hold them:

- customers registered and cards opened by the as-of instant, in any status, with their values as delivered and a flag
  when updated after it (ADR-0004, State);
- the last four digits in place of the card number (POL-11), and no amount_usd, fraud_score, or last_transaction_date
  (POL-20, POL-26, POL-40);
- card transactions dated within the 90 days (90 × 24 hours) that end at the as-of instant, by their own timestamp;
- Mexico spelled one way in transaction_country (ADR-0006, One spelling per country);
- conflict flags for an expiration before the business date and a transaction outside its card's dates (POL-30).

Its pipeline version hashes everything that shapes it: this code, the personas' rule, the writer, the contract, and
DuckDB's version. The business clock comes from the profile that ADR-0003's rule wrote for the same snapshot.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any

import duckdb

from faro import personas
from faro.export import items as writer

PRODUCER = "tiny"
WINDOW = timedelta(days=90)
CARD_TYPES = ("Tarjeta Crédito", "Tarjeta Débito")
TIMESTAMP = "%Y-%m-%d %H:%M:%S"


class TinyExportError(Exception):
    pass


def pipeline_version() -> str:
    digest = hashlib.sha256()
    sources = [Path(__file__), Path(personas.__file__), Path(writer.__file__)]
    for source in sources:
        digest.update(f"{source.name}\n".encode())
        digest.update(source.read_bytes())
    digest.update(
        files("faro.contracts").joinpath("tools-data.schema.json").read_bytes()
    )
    digest.update(f"duckdb {duckdb.__version__}\n".encode())
    return digest.hexdigest()[:16]


def clock_from_profile(path: Path, snapshot: str) -> tuple[date, datetime]:
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("snapshot_id") != snapshot:
        raise TinyExportError(f"{path} profiles another snapshot; run make analysis")
    return (
        date.fromisoformat(profile["business_date"]),
        datetime.strptime(profile["as_of"], TIMESTAMP),
    )


def partitions_needed(key_date: date, as_of: datetime) -> bool:
    """
    Events fall within their processing day (ADR-0006), so partitions from just before the window on hold every one.
    """
    return key_date >= (as_of - WINDOW).date() - timedelta(days=2)


def build(
    con: duckdb.DuckDBPyConnection,
    customers: Sequence[str],
    *,
    stamp: dict[str, str],
    business_date: date,
    as_of: datetime,
) -> list[dict[str, Any]]:
    """
    Reads the raw_customers, raw_products, and raw_transactions views that analysis.source.connect opens.
    """
    parameters = {"customers": list(customers), "as_of": as_of}
    customer_rows = con.execute(
        "select customer_id, customer_status, country, "
        "  cast(last_updated as timestamp) > $as_of as updated_after_as_of "
        "from raw_customers "
        "where list_contains($customers, customer_id) "
        "  and cast(registration_date as timestamp) <= $as_of "
        "order by customer_id",
        parameters,
    ).fetchall()
    if len(customer_rows) != len(set(customers)):
        raise TinyExportError(
            "a persona isn't among the customers registered by the as-of instant"
        )
    con.execute(
        "create or replace temp table tiny_cards as "
        "select customer_id, product_id, product_type, right(product_number, 4) as last_four, currency, "
        "  cast(current_balance as decimal(15,2)) as current_balance, "
        "  cast(credit_limit as decimal(15,2)) as credit_limit, product_status, "
        "  cast(opening_date as date) as opening_date, cast(expiration_date as date) as expiration_date, "
        "  coalesce(cast(expiration_date as date) < $business_date, false) as past_expiration, "
        "  cast(last_updated as timestamp) > $as_of as updated_after_as_of "
        "from raw_products "
        "where list_contains($customers, customer_id) and list_contains($card_types, product_type) "
        "  and cast(opening_date as date) <= $as_of",
        {**parameters, "business_date": business_date, "card_types": list(CARD_TYPES)},
    )
    card_rows = con.execute(
        "select customer_id, product_id, product_type, last_four, currency, current_balance, credit_limit, "
        "  product_status, opening_date, expiration_date, past_expiration, updated_after_as_of "
        "from tiny_cards order by customer_id, product_id"
    ).fetchall()
    transaction_rows = con.execute(
        "select k.customer_id, t.customer_id as delivered_customer, t.transaction_id, t.product_id, "
        "  cast(t.transaction_date as timestamp) as transaction_date, t.transaction_type, "
        "  cast(t.amount as decimal(15,2)) as amount, t.currency, t.channel, t.merchant_name, "
        "  t.merchant_category, "
        "  case when t.transaction_country = 'Mexico' then 'México' else t.transaction_country end, "
        "  t.transaction_status, t.response_code, cast(t.is_fraud as boolean) as is_fraud, "
        "  cast(cast(t.transaction_date as timestamp) as date) < k.opening_date, "
        "  coalesce(cast(cast(t.transaction_date as timestamp) as date) > k.expiration_date, false) "
        "from raw_transactions t join tiny_cards k on k.product_id = t.product_id "
        "where cast(t.transaction_date as timestamp) > $start "
        "  and cast(t.transaction_date as timestamp) <= $as_of "
        "order by k.customer_id, t.transaction_id",
        {"start": as_of - WINDOW, "as_of": as_of},
    ).fetchall()

    built: list[dict[str, Any]] = [
        {
            "pk": "META",
            "sk": "META",
            "kind": "metadata",
            "schema_version": 1,
            "stamp": stamp,
            "clock": clock(business_date, as_of),
        }
    ]
    for customer_id, status, country, updated in customer_rows:
        built.append(
            {
                "pk": customer_id,
                "sk": "CUSTOMER",
                "kind": "customer",
                "customer_id": customer_id,
                "customer_status": status,
                "country": country,
                "updated_after_as_of": updated,
            }
        )
    for row in card_rows:
        (customer_id, card_id, product_type, last_four, currency, balance, limit) = row[
            :7
        ]
        (status, opened, expires, past_expiration, updated) = row[7:]
        built.append(
            {
                "pk": customer_id,
                "sk": f"CARD#{card_id}",
                "kind": "card",
                "card_id": card_id,
                "product_type": product_type,
                "last_four": last_four,
                "currency": currency,
                "current_balance": balance,
                "credit_limit": limit,
                "product_status": status,
                "opening_date": opened.isoformat(),
                "expiration_date": expires.isoformat() if expires else None,
                "past_expiration": past_expiration,
                "updated_after_as_of": updated,
            }
        )
    for row in transaction_rows:
        (
            customer_id,
            delivered,
            transaction_id,
            card_id,
            when,
            kind,
            amount,
            currency,
        ) = row[:8]
        (channel, merchant, category, country, status, code, fraud, before, after) = (
            row[8:]
        )
        if delivered != customer_id:
            raise TinyExportError(
                "a transaction names another customer than its card's"
            )
        listed = when.strftime(TIMESTAMP)
        built.append(
            {
                "pk": customer_id,
                "sk": f"TXN#{transaction_id}",
                "kind": "transaction",
                "card_key": f"{customer_id}#{card_id}",
                "listed_at": f"{listed}#{transaction_id}",
                "transaction_id": transaction_id,
                "card_id": card_id,
                "transaction_date": listed,
                "transaction_type": kind,
                "amount": amount,
                "currency": currency,
                "channel": channel,
                "merchant_name": merchant,
                "merchant_category": category,
                "transaction_country": country,
                "transaction_status": status,
                "response_code": code,
                "is_fraud": fraud,
                "before_card_opening": before,
                "after_card_expiration": after,
            }
        )
    return built


def clock(business_date: date, as_of: datetime) -> dict[str, str]:
    return {
        "business_date": business_date.isoformat(),
        "as_of": as_of.strftime(TIMESTAMP),
    }
