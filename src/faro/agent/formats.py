"""
How a reply states each fact (ADR-0004's amendment of 2026-10-01, the table under decision 8): an amount in the card's
currency with its ISO code, grouped as the customer's country writes numbers (POL-20); the business date that balances
and transactions are read as of (POL-19); a transaction's time and the window's ends to the minute; an expiration by
month and year, or words when the record holds none (POL-32); statuses, types, and a code's meaning in the words the
contracts give them. Text from the bank's records is shown as recorded, with a run of 13 or more digits masked (POL-10,
POL-11). ADR-0005's oracle formats the facts it expects from that table and those words, not from this code.
"""

from decimal import Decimal
from typing import Any

from faro.contracts import words
from faro.masking import mask

# Colombia and Argentina group with points and use a decimal comma; México the other way round.
DECIMAL_COMMA = ("Colombia", "Argentina")

WORDS = words()
CARD_TYPES: dict[str, dict[str, str]] = WORDS["product_type"]
ENDING: dict[str, str] = WORDS["card_ending"]
STATUSES: dict[str, dict[str, str]] = WORDS["product_status"]
EXPIRATION_UNRECORDED: dict[str, str] = WORDS["expiration_unrecorded"]
TRANSACTION_STATUSES: dict[str, dict[str, str]] = WORDS["transaction_status"]
TRANSACTION_TYPES: dict[str, dict[str, str]] = WORDS["transaction_type"]
# POL-02: the ISO 8583 meaning of each listed code, and nothing else (POL-29).
MEANINGS: dict[str, dict[str, str]] = WORDS["response_meaning"]
REASON: dict[str, str] = WORDS["reason_label"]
NO_CREDIT: dict[str, str] = WORDS["credit_none"]
OVER_BY: dict[str, str] = WORDS["over_limit_label"]
UNRECORDED: dict[str, str] = WORDS["merchant_unrecorded"]
EXPIRES: dict[str, str] = WORDS["expiration_label"]
SEPARATOR = " · "


def amount(value: Any, currency: str, country: str) -> str:
    grouped = f"{Decimal(str(value)):,.2f}"
    if country in DECIMAL_COMMA:
        grouped = grouped.translate(str.maketrans(",.", ".,"))
    return f"{grouped} {currency}"


def day(date: str) -> str:
    year, month, of = date[:10].split("-")
    return f"{of}/{month}/{year}"


def moment(timestamp: str) -> str:
    return f"{day(timestamp)} {timestamp[11:16]}"


def expiration(date: str | None, language: str) -> str:
    if date is None:
        return EXPIRATION_UNRECORDED[language]
    year, month, _ = date.split("-")
    return f"{month}/{year}"


def card_name(card: dict[str, Any], language: str) -> str:
    return f"{CARD_TYPES[language][card['product_type']]} {ENDING[language]} {card['last_four']}"


def card_line(card: dict[str, Any], language: str) -> str:
    """
    A card in a status answer: its name, status, and expiration (POL-21).
    """
    return (
        f"- {card_name(card, language).capitalize()}: {STATUSES[language][card['product_status']]}; "
        f"{EXPIRES[language]}: {expiration(card['expiration_date'], language)}"
    )


def reason_line(meaning: str, language: str) -> str:
    """
    A decline's reason, on the line under its transaction, so the two read as one list: its label and the code's meaning
    (POL-02, POL-29).
    """
    return f"- {REASON[language].capitalize()}: {MEANINGS[language][meaning]}"


def credit_line(
    card: dict[str, Any], credit: dict[str, Any], language: str, country: str
) -> str:
    """
    A card in an answer about the credit of several: its credit available, or none and the amount its balance exceeds
    its limit by (POL-14, POL-22, POL-23).
    """
    name = card_name(card, language).capitalize()
    currency = credit["currency"]
    if credit["availability"] == "over_limit":
        over = amount(credit["over_limit_by"], currency, country)
        return f"- {name}: {NO_CREDIT[language]}; {OVER_BY[language]} {over}"
    return f"- {name}: {amount(credit['available_credit'], currency, country)}"


def recorded(text: str) -> str:
    return mask(text)


def merchant(transaction: dict[str, Any], language: str) -> str:
    name = transaction["merchant_name"]
    return UNRECORDED[language] if name is None else recorded(name)


def transaction_name(transaction: dict[str, Any], language: str, country: str) -> str:
    """
    A transaction the customer means: its time, its merchant for a purchase or its type otherwise, as a page names it,
    and its amount (POL-27, POL-39).
    """
    kind = transaction["transaction_type"]
    named = (
        merchant(transaction, language)
        if kind == "Purchase"
        else TRANSACTION_TYPES[language][kind]
    )
    return (
        f"{moment(transaction['transaction_date'])}, {named}, "
        f"{amount(transaction['amount'], transaction['currency'], country)}"
    )


def transaction_line(transaction: dict[str, Any], language: str, country: str) -> str:
    """
    A transaction in a page (POL-25): its time and type, the merchant for a purchase only, its amount, its status, and
    its country only when it isn't the customer's.
    """
    parts = [
        moment(transaction["transaction_date"]),
        TRANSACTION_TYPES[language][transaction["transaction_type"]].capitalize(),
    ]
    if transaction["transaction_type"] == "Purchase":
        parts.append(merchant(transaction, language))
    parts += [
        amount(transaction["amount"], transaction["currency"], country),
        TRANSACTION_STATUSES[language][transaction["transaction_status"]].capitalize(),
    ]
    if transaction["transaction_country"] != country:
        parts.append(recorded(transaction["transaction_country"]))
    return "- " + SEPARATOR.join(parts)
