"""
The facts a reply must state, written as ADR-0004's table under decision 8 says a reply states them (as amended on
2026-10-01), in the words the contracts give recorded values (reply-words.json, which the agent reads too). The oracle
writes them from the table, not from the agent's code, so a format the agent gets wrong is a disagreement.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import cache
from importlib.resources import files
from typing import Any

from faro.evaluation.state import (
    AS_OF,
    BUSINESS_DATE,
    WINDOW_FROM,
    Card,
    Transaction,
)

DECIMAL_COMMA = ("Colombia", "Argentina")
MEANINGS = {
    "05": "do_not_honor",
    "14": "invalid_card_number",
    "51": "insufficient_funds",
    "54": "expired_card",
}
# A run of 13 or more digits, spaced, dashed, or neither, keeps its last four (POL-11).
DIGIT_RUN = re.compile(r"\d(?:[ -]?\d){12,}")


@cache
def contract_words() -> dict[str, Any]:
    text = (
        files("faro.contracts").joinpath("reply-words.json").read_text(encoding="utf-8")
    )
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def masked(text: str) -> str:
    return DIGIT_RUN.sub(lambda m: "****" + re.sub(r"\D", "", m.group())[-4:], text)


def day(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def moment(value: datetime) -> str:
    return value.strftime("%d/%m/%Y %H:%M")


@dataclass(frozen=True)
class Facts:
    """
    For one conversation: its language, and the customer's country, which groups amounts and says when a transaction
    is abroad.
    """

    language: str
    country: str | None
    words: Mapping[str, Any]

    def _word(self, kind: str, value: str | None) -> str:
        found: str = self.words[kind][self.language][value]
        return found

    def _phrase(self, kind: str) -> str:
        found: str = self.words[kind][self.language]
        return found

    def amount(self, value: Decimal, currency: str | None) -> str:
        grouped = f"{value:,.2f}"
        if self.country in DECIMAL_COMMA:
            grouped = grouped.translate(str.maketrans(",.", ".,"))
        return f"{grouped} {currency}"

    def expiration(self, card: Card) -> str:
        if card.expiration is None:
            return self._phrase("expiration_unrecorded")
        return card.expiration.strftime("%m/%Y")

    def card(self, card: Card) -> str:
        return f"{self._word('product_type', card.type)} {self._phrase('card_ending')} {card.last_four}"

    def status(self, card: Card) -> str:
        return self._word("product_status", card.status)

    def card_line(self, card: Card) -> str:
        name = self.card(card)
        return (
            f"- {name[0].upper()}{name[1:]}: {self.status(card)}; "
            f"{self._phrase('expiration_label')}: {self.expiration(card)}"
        )

    def cards(self, cards: Sequence[Card]) -> str:
        return "\n".join(self.card_line(c) for c in cards)

    def card_list(self, cards: Sequence[Card]) -> str:
        names = [self.card(c) for c in cards]
        return "\n".join(f"- {name[0].upper()}{name[1:]}" for name in names)

    def merchant(self, transaction: Transaction) -> str:
        if transaction.merchant is None:
            return self._phrase("merchant_unrecorded")
        return masked(transaction.merchant)

    def transaction(self, transaction: Transaction) -> str:
        assert transaction.amount is not None
        named = (
            self.merchant(transaction)
            if transaction.type == "Purchase"
            else self._word("transaction_type", transaction.type)
        )
        return ", ".join(
            [
                moment(transaction.at),
                named,
                self.amount(transaction.amount, transaction.currency),
            ]
        )

    def found(self, transaction: Transaction) -> str:
        return f"- {self.transaction(transaction)}"

    def choices(self, transactions: Sequence[Transaction]) -> str:
        return "\n".join(self.found(t) for t in transactions)

    def page_line(self, transaction: Transaction) -> str:
        assert transaction.amount is not None
        kind = self._word("transaction_type", transaction.type)
        status = self._word("transaction_status", transaction.status)
        parts = [moment(transaction.at), kind[0].upper() + kind[1:]]
        if transaction.type == "Purchase":
            parts.append(self.merchant(transaction))
        parts += [
            self.amount(transaction.amount, transaction.currency),
            status[0].upper() + status[1:],
        ]
        if transaction.country != self.country and transaction.country is not None:
            parts.append(masked(transaction.country))
        return "- " + " · ".join(parts)

    def page(self, transactions: Sequence[Transaction]) -> str:
        return "\n".join(self.page_line(t) for t in transactions)

    def transaction_status(self, transaction: Transaction) -> str:
        return self._word("transaction_status", transaction.status)

    def meaning(self, transaction: Transaction) -> str:
        return self._word("response_meaning", MEANINGS[transaction.code or ""])

    def reason(self, transaction: Transaction) -> str:
        label = self._phrase("reason_label")
        return f"- {label[0].upper()}{label[1:]}: {self.meaning(transaction)}"

    def as_of(self) -> str:
        return day(BUSINESS_DATE)

    def window(self) -> dict[str, str]:
        return {"{window.from}": moment(WINDOW_FROM), "{window.to}": moment(AS_OF)}
