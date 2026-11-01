"""
A card and its transactions, as the nodes match them to what the customer says and show them to the model.
"""

import re
from datetime import datetime
from typing import Any

TYPES = {"credit": "Tarjeta Crédito", "debit": "Tarjeta Débito"}
KINDS = {product_type: kind for kind, product_type in TYPES.items()}
LAST_FOUR = re.compile(r"^[0-9]{4}$")


def card_facts(card: dict[str, Any]) -> dict[str, Any]:
    return {k: card[k] for k in ("product_type", "last_four", "product_status")}


def kind(card: dict[str, Any]) -> str:
    return "credit" if card["product_type"] == TYPES["credit"] else "debit"


def seen(card: dict[str, Any], transaction: dict[str, Any] | None = None) -> str:
    """
    The values the reply's model may choose words around: a card's type and status, and a transaction's status. Never
    an amount, a date, last four digits, a merchant, a country, or a code's meaning, which stands on a line of its own.
    """
    text = f"A {kind(card)} card whose status is {card['product_status']}."
    if transaction is not None:
        text += f" The transaction's status is {transaction['transaction_status']}."
    return text


def hints(details: dict[str, Any]) -> tuple[str | None, str | None]:
    last_four = details.get("last_four")
    last_four = last_four if last_four and LAST_FOUR.match(last_four) else None
    return TYPES.get(details.get("card_type") or ""), last_four


def fits(card: dict[str, Any], card_type: str | None, last_four: str | None) -> bool:
    return (card_type is None or card["product_type"] == card_type) and (
        last_four is None or card["last_four"] == last_four
    )


WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def weekday(timestamp: str) -> str:
    return WEEKDAYS[datetime.strptime(timestamp[:10], "%Y-%m-%d").weekday()]


def today(business_date: str) -> str:
    """
    POL-19: what the customer's "yesterday" or "last Friday" counts from, which code gives the model with each listed
    transaction's weekday, so the model matches words to dates and never computes one.
    """
    return (
        f"Today is {weekday(business_date)} {business_date}, the bank's business date."
    )


def listing(transactions: list[dict[str, Any]]) -> str:
    """
    The window's transactions as the model reads them: numbered, never by ID.
    """
    return "\n".join(
        f"{n}. {weekday(t['transaction_date'])} {t['transaction_date']}, {t['transaction_type']}, "
        f"{t['amount']} {t['currency']}, "
        f"{t['merchant_name'] or 'merchant not recorded'}, {t['transaction_status']}, {t['transaction_country']}"
        for n, t in enumerate(transactions, 1)
    )


def shown(transaction: dict[str, Any]) -> dict[str, Any]:
    return {
        k: transaction[k]
        for k in (
            "transaction_date",
            "transaction_type",
            "merchant_name",
            "amount",
            "currency",
        )
    }
