"""
The oracle's reading of bronze agrees with the pipeline's gold export, customer by customer, on the bank. The two share
no code, so a transformation either gets wrong shows up here as a disagreement rather than agreeing with itself.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Any

import pytest

from faro.evaluation import bronze, state
from faro.split import held_out
from faro.tools.fixtures import SignInData
from faro.tools.sandbox import MemoryOverlay
from faro.tools.store import MemoryData

from .bank import EXAMPLE_CUSTOMER, Bank, fixture_card, fixture_transaction

pytestmark = pytest.mark.xdist_group("evaluation_bank")


def number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def card_as_exported(card: state.Card) -> dict[str, Any]:
    return {
        "card_id": card.product_id,
        "product_type": card.type,
        "last_four": card.last_four,
        "currency": card.currency,
        "current_balance": number(card.balance),
        "credit_limit": number(card.limit),
        "product_status": card.status,
        "opening_date": card.opening.isoformat() if card.opening else None,
        "expiration_date": card.expiration.isoformat() if card.expiration else None,
    }


def transaction_as_exported(t: state.Transaction) -> dict[str, Any]:
    return {
        "transaction_id": t.transaction_id,
        "transaction_date": t.at.strftime("%Y-%m-%d %H:%M:%S"),
        "transaction_type": t.type,
        "amount": number(t.amount),
        "currency": t.currency,
        "merchant_name": t.merchant,
        "transaction_country": t.country,
        "transaction_status": t.status,
        "response_code": t.code,
        "is_fraud": t.is_fraud,
    }


def test_the_oracle_reads_each_customer_as_the_tools_do(bank: Bank) -> None:
    exported: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"cards": {}, "transactions": defaultdict(list)}
    )
    for item in bank.items:
        if item["kind"] == "customer":
            exported[item["pk"]]["customer"] = item
        elif item["kind"] == "card":
            exported[item["pk"]]["cards"][item["card_id"]] = item
        elif item["kind"] == "transaction":
            exported[item["pk"]]["transactions"][item["card_id"]].append(item)
    connections = {side: bronze.connect(bank.database, side) for side in bronze.SIDES}

    for customer_id, tools in exported.items():
        side = "held_out" if held_out(customer_id) else "development"
        read = state.read(connections[side], customer_id)

        assert read is not None, customer_id
        assert (read.status, read.country) == (
            tools["customer"]["customer_status"],
            tools["customer"]["country"],
        )
        assert {c.product_id: card_as_exported(c) for c in read.cards} == {
            card_id: {k: item[k] for k in card_as_exported(read.cards[0])}
            for card_id, item in tools["cards"].items()
        }
        for card in read.cards:
            listed = sorted(
                tools["transactions"][card.product_id],
                key=lambda t: t["listed_at"],
                reverse=True,
            )
            assert [transaction_as_exported(t) for t in card.transactions] == [
                {k: t[k] for k in transaction_as_exported(card.transactions[0])}
                for t in listed
            ]
    assert EXAMPLE_CUSTOMER in exported
    assert sum(len(c["transactions"]) for c in exported.values()) > 10


def test_the_oracle_reads_fixtures_as_the_tools_merge_them(bank: Bank) -> None:
    customer_id, card_id = "CLI-EVAL00000003", "PRD-EVAL00000301"
    twin = fixture_card(customer_id, 1, product_type=state.CREDIT, last_four="0301")
    fixtures = [
        twin,
        fixture_transaction(customer_id, card_id, 1, "2026-06-16 18:22:05"),
        fixture_transaction(
            customer_id,
            twin["card_id"],
            2,
            "2026-06-10 13:05:12",
            transaction_status="Declined",
            response_code="61",
        ),
        # Outside the window, and another customer's: neither is read.
        fixture_transaction(customer_id, card_id, 3, "2026-03-01 09:00:00"),
        fixture_transaction(
            "CLI-EVAL00000007", "PRD-EVAL00000701", 4, "2026-06-15 10:00:00"
        ),
    ]
    sign_in = "5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68"
    tools = SignInData(
        MemoryData(bank.items),
        MemoryOverlay([{**f, "sign_in": sign_in, "ttl": 0} for f in fixtures]),
        sign_in,
        customer_id,
    )
    with bronze.connect(bank.database, "development") as con:
        read = state.read(con, customer_id)
    assert read is not None

    merged = state.merged(read, fixtures)

    # get_card's fields and get_available_credit's; no read returns is_fraud.
    shown = [
        {
            **(tools.card(customer_id, c["card_id"]) or {}),
            **(tools.credit(customer_id, c["card_id"]) or {}),
        }
        for c in sorted(
            tools.cards(customer_id),
            key=lambda c: (c["product_type"], c["last_four"], c["card_id"]),
        )
    ]
    assert [card_as_exported(c) for c in merged.cards] == [
        {k: c[k] for k in card_as_exported(merged.cards[0])} for c in shown
    ]
    for card in merged.cards:
        read_back = [
            {k: v for k, v in transaction_as_exported(t).items() if k != "is_fraud"}
            for t in card.transactions
        ]
        listed = tools.transactions(customer_id, card.product_id, None, 100)
        assert read_back == [{k: t[k] for k in read_back[0]} for t in listed]
    assert {c.product_id: len(c.transactions) for c in merged.cards} == {
        card_id: len(read.card(card_id).transactions) + 1,
        twin["card_id"]: 1,
    }


def test_a_customer_on_the_other_side_isnt_read(bank: Bank) -> None:
    with bronze.connect(bank.database, "held_out") as con:
        assert state.read(con, EXAMPLE_CUSTOMER) is None
