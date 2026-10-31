"""
find_transactions (ADR-0004, Tools; POL-02, POL-12, POL-19, POL-25, POL-27, POL-28, POL-40). A card's transactions in the
90 days that end at the as-of instant, newest first, 10 at a time, read through the by_card index, which holds no
is_fraud. Only a Declined transaction's code is explained, and only a listed one. A customer who isn't served in full may
only block a card, so their transactions aren't listed, and their status isn't named.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from faro.tools.cards import SERVED_IN_FULL, CustomerMissingError
from faro.tools.sandbox import Stores
from faro.tools.store import Record, ToolsData

PAGE = 10
WINDOW = timedelta(days=90)
TIMESTAMP = "%Y-%m-%d %H:%M:%S"
MEANINGS = {
    "05": "do_not_honor",
    "14": "invalid_card_number",
    "51": "insufficient_funds",
    "54": "expired_card",
}


def _stamped(data: ToolsData) -> dict[str, Any]:
    metadata = data.metadata()
    return {"stamp": metadata["stamp"], "clock": metadata["clock"]}


def window(clock: dict[str, str]) -> dict[str, str]:
    to = datetime.strptime(clock["as_of"], TIMESTAMP)
    return {"from": (to - WINDOW).strftime(TIMESTAMP), "to": clock["as_of"]}


def number(value: Any) -> int | float:
    # DynamoDB hands numbers back as Decimals, which a Lambda's response can't carry.
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, int | float):
        return value
    raise TypeError(f"not an amount: a {type(value).__name__}")


def meaning(record: Record) -> str | None:
    if record["transaction_status"] != "Declined":
        return None
    return MEANINGS.get(record["response_code"] or "")


def listed(record: Record) -> dict[str, Any]:
    return {
        "transaction_id": record["transaction_id"],
        "transaction_date": record["transaction_date"],
        "transaction_type": record["transaction_type"],
        "transaction_status": record["transaction_status"],
        "response_code": record["response_code"],
        "response_meaning": meaning(record),
        "amount": number(record["amount"]),
        "currency": record["currency"],
        "channel": record["channel"],
        "merchant_name": record["merchant_name"],
        "merchant_category": record["merchant_category"],
        "transaction_country": record["transaction_country"],
        "before_card_opening": record["before_card_opening"],
        "after_card_expiration": record["after_card_expiration"],
    }


def find_transactions(stores: Stores, arguments: dict[str, Any]) -> dict[str, Any]:
    data, customer_id, card_id = (
        stores.data,
        arguments["customer_id"],
        arguments["card_id"],
    )
    stamped = _stamped(data)
    customer = data.customer(customer_id)
    if customer is None:
        raise CustomerMissingError()
    if customer["customer_status"] not in SERVED_IN_FULL:
        return {"outcome": "refused", **stamped, "refusal": "not_served"}
    if data.card(customer_id, card_id) is None:
        return {"outcome": "not_found", **stamped}
    found = data.transactions(customer_id, card_id, arguments.get("cursor"), PAGE + 1)
    page = found[:PAGE]
    return {
        "outcome": "ok",
        **stamped,
        "card_id": card_id,
        "window": window(stamped["clock"]),
        "transactions": [listed(record) for record in page],
        "next_cursor": page[-1]["listed_at"] if len(found) > PAGE else None,
    }
