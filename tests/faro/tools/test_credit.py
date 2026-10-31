"""
get_available_credit computes the figure for an active credit card only, in decimal, and says why any other card has
none, in the contract's shapes (POL-01, POL-12, POL-18, POL-22 to POL-24, POL-33; CTL-01, AI-03, SEC-05).
"""

from decimal import Decimal
from typing import Any

import pytest

from faro.contracts import validator
from faro.tools.cards import CustomerMissingError
from faro.tools.credit import get_available_credit
from faro.tools.sandbox import MemoryOverlay, Stores
from faro.tools.store import MemoryData

from .conftest import OTHER, OWN, SIGN_IN, card, example_items, overlay_item


def fits(output: dict[str, Any]) -> bool:
    return validator("tools", "get_available_credit_output").is_valid(output)


def credit_card(card_id: str, balance: Any, limit: Any) -> dict[str, Any]:
    return {
        **card(OWN, card_id, "Tarjeta Crédito", "6610", "Active"),
        "current_balance": balance,
        "credit_limit": limit,
    }


def read(stores: Stores, card_id: str, customer_id: str = OWN) -> dict[str, Any]:
    output = get_available_credit(
        stores, {"customer_id": customer_id, "origin_jti": SIGN_IN, "card_id": card_id}
    )
    assert fits(output)
    return output


def with_cards(*items: dict[str, Any]) -> Stores:
    return Stores(MemoryData([*example_items(), *items]), MemoryOverlay())


def test_an_active_credit_card_gets_its_limit_less_its_balance_to_the_cent() -> None:
    stores = with_cards(credit_card("PRD-EXAMPLE00010", 1240.55, 5000))

    output = read(stores, "PRD-EXAMPLE00010")

    assert output["card"] == {
        "card_id": "PRD-EXAMPLE00010",
        "product_type": "Tarjeta Crédito",
        "last_four": "6610",
        "product_status": "Active",
        "currency": "USD",
        "availability": "available",
        "credit_limit": 5000,
        "current_balance": 1240.55,
        "available_credit": 3759.45,
        "over_limit_by": 0,
    }


def test_decimals_from_dynamodb_are_subtracted_exactly() -> None:
    stores = with_cards(credit_card("PRD-EXAMPLE00010", Decimal("0.1"), Decimal("0.3")))

    assert read(stores, "PRD-EXAMPLE00010")["card"]["available_credit"] == 0.2


def test_a_balance_over_the_limit_leaves_nothing_and_says_by_how_much() -> None:
    # POL-23.
    stores = with_cards(credit_card("PRD-EXAMPLE00010", 2150.4, 2000))

    shown = read(stores, "PRD-EXAMPLE00010")["card"]

    assert shown["availability"] == "over_limit"
    assert (shown["available_credit"], shown["over_limit_by"]) == (0, 150.4)


def test_a_balance_at_the_limit_is_available_with_nothing_left() -> None:
    stores = with_cards(credit_card("PRD-EXAMPLE00010", 2000, 2000))

    shown = read(stores, "PRD-EXAMPLE00010")["card"]

    assert (shown["availability"], shown["available_credit"]) == ("available", 0)


def test_a_missing_limit_gets_no_figure_and_is_never_read_as_zero() -> None:
    # POL-24: the contract's example card has no recorded limit.
    shown = read(with_cards(), "PRD-EXAMPLE00002")["card"]

    assert shown["availability"] == "no_limit"
    assert shown["credit_limit"] is None
    assert "available_credit" not in shown and "over_limit_by" not in shown


@pytest.mark.parametrize(
    ("card_id", "availability"),
    [("PRD-EXAMPLE00005", "debit_card"), ("PRD-EXAMPLE00007", "not_active")],
)
def test_a_debit_card_or_one_not_active_carries_no_balance_or_limit(
    card_id: str, availability: str
) -> None:
    # POL-22.
    shown = read(with_cards(), card_id)["card"]

    assert shown["availability"] == availability
    assert not {"credit_limit", "current_balance", "available_credit"} & set(shown)


def test_a_card_blocked_in_the_sign_ins_sandbox_has_no_figure() -> None:
    # POL-33: the overlay's status comes first.
    overlay = MemoryOverlay([overlay_item("PRD-EXAMPLE00010")])
    stores = Stores(
        MemoryData([*example_items(), credit_card("PRD-EXAMPLE00010", 10, 100)]),
        overlay,
    )

    shown = read(stores, "PRD-EXAMPLE00010")["card"]

    assert (shown["product_status"], shown["availability"]) == ("Blocked", "not_active")


def test_a_customer_not_served_in_full_is_refused_without_the_status() -> None:
    # POL-12.
    output = read(with_cards(), "PRD-EXAMPLE00008", OTHER)

    assert (output["outcome"], output["refusal"]) == ("refused", "not_served")
    assert "Suspended" not in str(output)


def test_another_customers_card_reads_as_not_found() -> None:
    # POL-08: the other customer's card, under the caller's own ID.
    assert read(with_cards(), "PRD-EXAMPLE00008")["outcome"] == "not_found"


def test_a_customer_the_data_doesnt_hold_is_a_fault() -> None:
    with pytest.raises(CustomerMissingError):
        get_available_credit(
            with_cards(),
            {
                "customer_id": "CLI-EXAMPLE00042",
                "origin_jti": SIGN_IN,
                "card_id": "PRD-EXAMPLE00002",
            },
        )
