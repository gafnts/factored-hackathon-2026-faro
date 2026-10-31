"""
find_transactions lists a card's transactions in the window, newest first, 10 at a time, explains a code only on a
Declined transaction and only a listed one, never returns is_fraud, refuses a customer who isn't served in full without
naming the status, and reads another customer's card as a card that doesn't exist (POL-02, POL-08, POL-12, POL-25,
POL-27, POL-28, POL-40; SEC-05).
"""

import json
from decimal import Decimal
from typing import Any

import pytest

from faro.contracts import validator
from faro.tools.sandbox import MemoryOverlay, Stores
from faro.tools.store import MemoryData
from faro.tools.transactions import PAGE, find_transactions

from .conftest import OTHER, OWN, SIGN_IN, example_items

CARD = "PRD-EXAMPLE00002"
EMPTY = "PRD-EXAMPLE00005"


def transaction(
    number: int,
    status: str = "Approved",
    code: str | None = "00",
    customer: str = OWN,
    card_id: str = CARD,
) -> dict[str, Any]:
    transaction_id = f"TRX-TEAM{number:016d}"
    date = f"2026-06-{1 + number % 16:02d} {number % 24:02d}:00:00"
    return {
        "pk": customer,
        "sk": f"TXN#{transaction_id}",
        "kind": "transaction",
        "card_key": f"{customer}#{card_id}",
        "listed_at": f"{date}#{transaction_id}",
        "transaction_id": transaction_id,
        "card_id": card_id,
        "transaction_date": date,
        "transaction_type": "Purchase",
        "amount": Decimal("12.50") if number % 2 else Decimal("40"),
        "currency": "USD",
        "channel": "POS",
        "merchant_name": None if number == 1 else "Comercio del Equipo",
        "merchant_category": "Retail",
        "transaction_country": "México",
        "transaction_status": status,
        "response_code": code,
        "is_fraud": number % 3 == 0,
        "before_card_opening": False,
        "after_card_expiration": False,
    }


def stores_with(*extra: dict[str, Any]) -> Stores:
    return Stores(MemoryData([*example_items(), *extra]), MemoryOverlay())


def find(
    stores: Stores, card_id: str = CARD, customer: str = OWN, **extra: str
) -> dict[str, Any]:
    output = find_transactions(
        stores,
        {"customer_id": customer, "origin_jti": SIGN_IN, "card_id": card_id, **extra},
    )
    validator("tools", "find_transactions_output").validate(output)
    return output


def test_a_card_lists_its_transactions_newest_first_ten_at_a_time() -> None:
    stores = stores_with(*(transaction(n) for n in range(1, 24)))

    pages = [find(stores)]
    while pages[-1]["next_cursor"] is not None:
        pages.append(find(stores, cursor=pages[-1]["next_cursor"]))

    assert [len(page["transactions"]) for page in pages] == [PAGE, PAGE, 4]
    listed = [
        (t["transaction_date"], t["transaction_id"])
        for page in pages
        for t in page["transactions"]
    ]
    assert listed == sorted(listed, reverse=True)
    assert len(set(listed)) == 24


def test_a_page_that_holds_the_last_transaction_has_no_cursor() -> None:
    stores = stores_with(*(transaction(n) for n in range(1, PAGE)))

    output = find(stores)

    assert len(output["transactions"]) == PAGE
    assert output["next_cursor"] is None


def test_the_window_ends_at_the_as_of_instant() -> None:
    output = find(stores_with())

    assert output["window"] == {
        "from": "2026-03-20 06:00:00",
        "to": "2026-06-18 06:00:00",
    }
    assert output["card_id"] == CARD


@pytest.mark.parametrize(
    ("status", "code", "meaning"),
    [
        ("Declined", "05", "do_not_honor"),
        ("Declined", "14", "invalid_card_number"),
        ("Declined", "51", "insufficient_funds"),
        ("Declined", "54", "expired_card"),
        ("Declined", "91", None),
        ("Declined", None, None),
        ("Pending", "05", None),
        ("Reversed", "51", None),
        ("Approved", "00", None),
    ],
)
def test_only_a_declined_transactions_listed_code_is_explained(
    status: str, code: str | None, meaning: str | None
) -> None:
    output = find(stores_with(transaction(30, status, code)))

    found = next(
        t for t in output["transactions"] if t["transaction_id"].startswith("TRX-TEAM")
    )
    assert (found["response_code"], found["response_meaning"]) == (code, meaning)


def test_no_transaction_carries_is_fraud() -> None:
    output = find(stores_with(*(transaction(n) for n in range(1, 7))))

    assert "is_fraud" not in json.dumps(output)


def test_amounts_are_numbers_and_a_missing_merchant_stays_missing() -> None:
    output = find(stores_with(transaction(1), transaction(2)))

    amounts = {t["transaction_id"][-1]: t["amount"] for t in output["transactions"]}
    assert amounts["1"] == 12.5 and amounts["2"] == 40
    assert all(type(a) in (int, float) for a in amounts.values())
    first = next(t for t in output["transactions"] if t["transaction_id"].endswith("1"))
    assert first["merchant_name"] is None


def test_a_card_with_no_transactions_gets_an_empty_page() -> None:
    output = find(stores_with(), card_id=EMPTY)

    assert output["transactions"] == []
    assert output["next_cursor"] is None


def test_another_customers_card_reads_as_a_card_that_doesnt_exist() -> None:
    stores = stores_with(transaction(5, customer=OTHER, card_id="PRD-EXAMPLE00008"))

    theirs = find(stores, card_id="PRD-EXAMPLE00008")
    missing = find(stores, card_id="PRD-EXAMPLE00404")

    assert theirs == missing
    assert theirs["outcome"] == "not_found"


def test_a_customer_not_served_in_full_is_refused_without_the_status() -> None:
    output = find(stores_with(), card_id="PRD-EXAMPLE00008", customer=OTHER)

    assert output["outcome"] == "refused"
    assert output["refusal"] == "not_served"
    assert "Suspended" not in json.dumps(output)
