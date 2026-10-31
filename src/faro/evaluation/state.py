"""
One customer's state as the policy reads it, from a bronze connection of one side (ADR-0005, The oracle). It applies
the contracts' rules in its own code, never the pipeline's or the tools': text trimmed and empty text missing, one
spelling per country, customers registered and cards opened by the as-of instant, a card's transactions in the 90 days
that end at the as-of instant counted by their own timestamp (POL-19, POL-25), and the conflicts POL-30 states. A
case's fixtures are read into it the same way (ADR-0005's amendment of 2026-10-01).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import duckdb

# ADR-0003's clock for the pinned snapshot, as the policy states it (How to read it); the evaluation's bank is built to
# the same clock.
BUSINESS_DATE = date(2026, 6, 17)
AS_OF = datetime(2026, 6, 18, 6, 0)
WINDOW = timedelta(days=90)
WINDOW_FROM = AS_OF - WINDOW

CREDIT, DEBIT = "Tarjeta Crédito", "Tarjeta Débito"
SERVED_IN_FULL = ("Active", "Inactive")
LISTED_CODES = ("05", "14", "51", "54")


def text(value: Any) -> str | None:
    if value is None:
        return None
    trimmed = str(value).strip()
    return trimmed or None


def country(value: Any) -> str | None:
    trimmed = text(value)
    return "México" if trimmed == "Mexico" else trimmed


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    product_id: str
    at: datetime
    type: str | None
    amount: Decimal | None
    currency: str | None
    merchant: str | None
    country: str | None
    status: str | None
    code: str | None
    is_fraud: bool | None

    @property
    def listed_code(self) -> bool:
        return self.code in LISTED_CODES


@dataclass(frozen=True)
class Card:
    product_id: str
    type: str
    last_four: str
    currency: str | None
    balance: Decimal | None
    limit: Decimal | None
    status: str | None
    opening: date | None
    expiration: date | None
    # The card's transactions in the window, newest first, as the tools list them.
    transactions: tuple[Transaction, ...] = field(default=(), repr=False)

    @property
    def credit(self) -> bool:
        return self.type == CREDIT

    @property
    def active(self) -> bool:
        return self.status == "Active"

    @property
    def past_expiration(self) -> bool:
        return self.expiration is not None and self.expiration < BUSINESS_DATE

    def conflicts(self, transaction: Transaction) -> list[str]:
        """
        POL-30's conflicts between this card's record and one of its transactions.
        """
        found = []
        if self.opening is not None and transaction.at.date() < self.opening:
            found.append("before_opening")
        if (
            transaction.code == "54"
            and self.expiration is not None
            and transaction.at.date() < self.expiration
        ):
            found.append("expired_code_before_expiration")
        return found


@dataclass(frozen=True)
class Customer:
    customer_id: str
    status: str | None
    country: str | None
    # In the order list_cards gives: by type, then last four digits, then ID.
    cards: tuple[Card, ...]

    @property
    def served_in_full(self) -> bool:
        return self.status in SERVED_IN_FULL

    def card(self, product_id: str) -> Card:
        return next(c for c in self.cards if c.product_id == product_id)


def read(con: duckdb.DuckDBPyConnection, customer_id: str) -> Customer | None:
    """
    None when the customer isn't on the connection's side or wasn't registered by the as-of instant.
    """
    found = con.execute(
        "select customer_status, country from customers "
        "where customer_id = $id and registration_date <= $as_of",
        {"id": customer_id, "as_of": AS_OF},
    ).fetchone()
    if found is None:
        return None
    rows = con.execute(
        "select product_id, product_type, product_number, currency, current_balance, credit_limit, "
        "product_status, opening_date, expiration_date from products "
        "where customer_id = $id and opening_date <= $as_of",
        {"id": customer_id, "as_of": AS_OF},
    ).fetchall()
    cards = [
        Card(
            product_id=row[0],
            type=kind,
            last_four=(text(row[2]) or "")[-4:],
            currency=text(row[3]),
            balance=row[4],
            limit=row[5],
            status=text(row[6]),
            opening=row[7],
            expiration=row[8],
            transactions=_transactions(con, row[0]),
        )
        for row in rows
        if (kind := text(row[1])) in (CREDIT, DEBIT)
    ]
    cards.sort(key=lambda c: (c.type, c.last_four, c.product_id))
    return Customer(customer_id, text(found[0]), country(found[1]), tuple(cards))


def _money(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _day(value: Any) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _newest_first(transactions: Sequence[Transaction]) -> tuple[Transaction, ...]:
    return tuple(
        sorted(transactions, key=lambda t: (t.at, t.transaction_id), reverse=True)
    )


def merged(customer: Customer, fixtures: Sequence[Mapping[str, Any]]) -> Customer:
    """
    The customer's state with a case's fixtures, each in the overlay contract's shape: a fixture card among the cards,
    and a fixture transaction on its card when it falls in the window. A fixture for another customer, or on a card the
    customer doesn't hold, is left out, as the tools leave it out.
    """
    own = [f for f in fixtures if f["customer_id"] == customer.customer_id]
    cards = [
        *customer.cards,
        *(
            Card(
                product_id=f["card_id"],
                type=f["product_type"],
                last_four=f["last_four"],
                currency=text(f["currency"]),
                balance=_money(f["current_balance"]),
                limit=_money(f["credit_limit"]),
                status=text(f["product_status"]),
                opening=_day(f["opening_date"]),
                expiration=_day(f["expiration_date"]),
            )
            for f in own
            if f["kind"] == "card"
        ),
    ]
    added = [
        Transaction(
            transaction_id=f["transaction_id"],
            product_id=f["card_id"],
            at=datetime.fromisoformat(f["transaction_date"]),
            type=text(f["transaction_type"]),
            amount=_money(f["amount"]),
            currency=text(f["currency"]),
            merchant=text(f["merchant_name"]),
            country=country(f["transaction_country"]),
            status=text(f["transaction_status"]),
            code=text(f["response_code"]),
            is_fraud=f["is_fraud"],
        )
        for f in own
        if f["kind"] == "transaction"
    ]
    inside = [t for t in added if WINDOW_FROM < t.at <= AS_OF]
    held = [
        replace(
            c,
            transactions=_newest_first(
                [*c.transactions, *(t for t in inside if t.product_id == c.product_id)]
            ),
        )
        for c in cards
    ]
    held.sort(key=lambda c: (c.type, c.last_four, c.product_id))
    return replace(customer, cards=tuple(held))


def _transactions(
    con: duckdb.DuckDBPyConnection, product_id: str
) -> tuple[Transaction, ...]:
    rows = con.execute(
        "select transaction_id, transaction_date, transaction_type, amount, currency, merchant_name, "
        "transaction_country, transaction_status, response_code, is_fraud from transactions "
        "where product_id = $id and transaction_date > $from and transaction_date <= $as_of "
        "order by transaction_date desc, transaction_id desc",
        {"id": product_id, "from": WINDOW_FROM, "as_of": AS_OF},
    ).fetchall()
    return tuple(
        Transaction(
            transaction_id=row[0],
            product_id=product_id,
            at=row[1],
            type=text(row[2]),
            amount=row[3],
            currency=text(row[4]),
            merchant=text(row[5]),
            country=country(row[6]),
            status=text(row[7]),
            code=text(row[8]),
            is_fraud=row[9],
        )
        for row in rows
    )
