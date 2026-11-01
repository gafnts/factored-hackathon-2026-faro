"""
A card and its transactions, as the nodes match them to what the customer says and show them to the model.
"""

import re
from datetime import datetime
from typing import Literal, NotRequired, TypedDict

from faro.agent.models import Details

Kind = Literal["credit", "debit"]
ProductType = Literal["Tarjeta Crédito", "Tarjeta Débito"]
ProductStatus = Literal["Active", "Blocked", "Closed", "Suspended"]
TYPES: dict[Kind, ProductType] = {
    "credit": "Tarjeta Crédito",
    "debit": "Tarjeta Débito",
}
KINDS = {product_type: kind for kind, product_type in TYPES.items()}
LAST_FOUR = re.compile(r"^[0-9]{4}$")


class Card(TypedDict):
    """
    A card as list_cards lists it (tools.schema.json, card_summary).
    """

    card_id: str
    product_type: ProductType
    last_four: str
    product_status: ProductStatus
    past_expiration: bool
    updated_after_as_of: bool


class CardRecord(Card):
    """
    A card as get_card reads it, with its dates.
    """

    opening_date: str
    expiration_date: str | None


class Credit(TypedDict):
    """
    A card's credit as get_available_credit computes it; only an active credit card carries the figures.
    """

    card_id: str
    product_type: ProductType
    last_four: str
    product_status: ProductStatus
    currency: str
    availability: Literal[
        "available", "over_limit", "no_limit", "debit_card", "not_active"
    ]
    credit_limit: NotRequired[float | None]
    current_balance: NotRequired[float]
    available_credit: NotRequired[float]
    over_limit_by: NotRequired[float]


class Transaction(TypedDict):
    """
    A transaction as find_transactions reads it.
    """

    transaction_id: str
    transaction_date: str
    transaction_type: str
    transaction_status: Literal["Approved", "Declined", "Pending", "Reversed"]
    response_code: str | None
    response_meaning: str | None
    amount: float
    currency: str
    channel: str
    merchant_name: str | None
    merchant_category: str | None
    transaction_country: str
    before_card_opening: bool
    after_card_expiration: bool


# Functional, since from is a keyword.
Window = TypedDict("Window", {"from": str, "to": str})


class CardFacts(TypedDict):
    product_type: ProductType
    last_four: str
    product_status: ProductStatus


class ShownTransaction(TypedDict):
    transaction_date: str
    transaction_type: str
    merchant_name: str | None
    amount: float
    currency: str


def card_facts(card: CardFacts) -> CardFacts:
    return {
        "product_type": card["product_type"],
        "last_four": card["last_four"],
        "product_status": card["product_status"],
    }


def kind(card: CardFacts) -> Kind:
    return "credit" if card["product_type"] == TYPES["credit"] else "debit"


def seen(card: CardFacts, transaction: Transaction | None = None) -> str:
    """
    The values the reply's model may choose words around: a card's type and status, and a transaction's status. Never
    an amount, a date, last four digits, a merchant, a country, or a code's meaning, which stands on a line of its own.
    """
    text = f"A {kind(card)} card whose status is {card['product_status']}."
    if transaction is not None:
        text += f" The transaction's status is {transaction['transaction_status']}."
    return text


def hints(details: Details) -> tuple[ProductType | None, str | None]:
    last_four = details.get("last_four")
    last_four = last_four if last_four and LAST_FOUR.match(last_four) else None
    card_type = details.get("card_type")
    return (TYPES[card_type] if card_type else None), last_four


def fits(card: CardFacts, card_type: ProductType | None, last_four: str | None) -> bool:
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


def listing(transactions: list[Transaction]) -> str:
    """
    The window's transactions as the model reads them: numbered, never by ID.
    """
    return "\n".join(
        f"{n}. {weekday(t['transaction_date'])} {t['transaction_date']}, {t['transaction_type']}, "
        f"{t['amount']} {t['currency']}, "
        f"{t['merchant_name'] or 'merchant not recorded'}, {t['transaction_status']}, {t['transaction_country']}"
        for n, t in enumerate(transactions, 1)
    )


def shown(transaction: Transaction) -> ShownTransaction:
    return {
        "transaction_date": transaction["transaction_date"],
        "transaction_type": transaction["transaction_type"],
        "merchant_name": transaction["merchant_name"],
        "amount": transaction["amount"],
        "currency": transaction["currency"],
    }
