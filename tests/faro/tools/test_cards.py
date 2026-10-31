"""
list_cards and get_card answer from the caller's partition only, in the contract's shapes, without the customer's status,
and show a card as the sign-in's sandbox holds it (POL-08, POL-12 to POL-16, POL-21, POL-33; SEC-05, CTL-04).
"""

from typing import Any

import pytest

from faro.contracts import validator
from faro.tools.cards import CustomerMissingError, get_card, list_cards
from faro.tools.sandbox import MemoryOverlay, Stores
from faro.tools.store import MemoryData

from .conftest import OTHER, OWN, SIGN_IN, overlay_item

STAMP = {"snapshot": "b3b8b248f604ef9a", "pipeline_version": "5e1a9c3b7d2f4a68"}
CLOCK = {"business_date": "2026-06-17", "as_of": "2026-06-18 06:00:00"}


def fits(tool: str, output: dict[str, Any]) -> bool:
    return validator("tools", f"{tool}_output").is_valid(output)


def test_a_customer_lists_their_cards_in_every_status_ordered(stores: Stores) -> None:
    output = list_cards(stores, {"customer_id": OWN, "origin_jti": SIGN_IN})

    assert fits("list_cards", output)
    assert (output["stamp"], output["clock"]) == (STAMP, CLOCK)
    assert output["customer"] == {"served_in_full": True, "country": "México"}
    assert [
        (c["product_type"], c["last_four"], c["card_id"]) for c in output["cards"]
    ] == [
        ("Tarjeta Crédito", "4821", "PRD-EXAMPLE00001"),
        ("Tarjeta Crédito", "4821", "PRD-EXAMPLE00002"),
        ("Tarjeta Crédito", "9034", "PRD-EXAMPLE00007"),
        ("Tarjeta Débito", "1177", "PRD-EXAMPLE00005"),
    ]


def test_a_suspended_customer_is_listed_as_not_served_in_full_without_the_status(
    stores: Stores,
) -> None:
    output = list_cards(stores, {"customer_id": OTHER, "origin_jti": SIGN_IN})

    assert output["customer"] == {"served_in_full": False, "country": "Colombia"}
    assert "Suspended" not in str(output)
    assert [c["card_id"] for c in output["cards"]] == ["PRD-EXAMPLE00008"]


@pytest.mark.parametrize("status", ["Active", "Inactive", "Suspended", "Closed"])
def test_only_active_and_inactive_customers_are_served_in_full(status: str) -> None:
    data = MemoryData(
        [
            {"pk": "META", "sk": "META", "stamp": STAMP, "clock": CLOCK},
            {
                "pk": OWN,
                "sk": "CUSTOMER",
                "customer_status": status,
                "country": "Argentina",
            },
        ]
    )

    output = list_cards(
        Stores(data, MemoryOverlay()), {"customer_id": OWN, "origin_jti": SIGN_IN}
    )

    assert output["customer"]["served_in_full"] is (status in ("Active", "Inactive"))


def test_a_customer_the_data_doesnt_hold_is_a_fault_not_an_answer(
    stores: Stores,
) -> None:
    with pytest.raises(CustomerMissingError):
        list_cards(stores, {"customer_id": "CLI-EXAMPLE00404", "origin_jti": SIGN_IN})


def test_a_customer_reads_one_of_their_cards(stores: Stores) -> None:
    output = get_card(
        stores,
        {"customer_id": OWN, "origin_jti": SIGN_IN, "card_id": "PRD-EXAMPLE00002"},
    )

    assert fits("get_card", output)
    assert output["card"] == {
        "card_id": "PRD-EXAMPLE00002",
        "product_type": "Tarjeta Crédito",
        "last_four": "4821",
        "product_status": "Active",
        "opening_date": "2023-02-14",
        "expiration_date": "2026-02-13",
        "past_expiration": True,
        "updated_after_as_of": False,
    }


def test_another_customers_card_reads_as_a_card_that_doesnt_exist(
    stores: Stores,
) -> None:
    theirs = get_card(
        stores,
        {"customer_id": OWN, "origin_jti": SIGN_IN, "card_id": "PRD-EXAMPLE00008"},
    )
    missing = get_card(
        stores,
        {"customer_id": OWN, "origin_jti": SIGN_IN, "card_id": "PRD-EXAMPLE00404"},
    )

    assert theirs == missing == {"outcome": "not_found", "stamp": STAMP, "clock": CLOCK}
    assert fits("get_card", theirs)


def test_a_block_in_the_sign_ins_sandbox_shows_in_both_reads(stores: Stores) -> None:
    overlay = MemoryOverlay([overlay_item("PRD-EXAMPLE00002")])
    sandboxed = Stores(stores.data, overlay)
    card = {"customer_id": OWN, "origin_jti": SIGN_IN, "card_id": "PRD-EXAMPLE00002"}

    listed = list_cards(sandboxed, {"customer_id": OWN, "origin_jti": SIGN_IN})
    read = get_card(sandboxed, card)

    assert fits("list_cards", listed) and fits("get_card", read)
    statuses = {c["card_id"]: c["product_status"] for c in listed["cards"]}
    assert statuses["PRD-EXAMPLE00002"] == read["card"]["product_status"] == "Blocked"
    assert statuses["PRD-EXAMPLE00005"] == "Active"
    assert get_card(stores, card)["card"]["product_status"] == "Active"


def test_another_sign_ins_sandbox_and_another_customers_item_are_never_read(
    stores: Stores,
) -> None:
    overlay = MemoryOverlay(
        [
            overlay_item(
                "PRD-EXAMPLE00002", sign_in="0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50"
            ),
            overlay_item("PRD-EXAMPLE00005", customer_id=OTHER),
        ]
    )
    sandboxed = Stores(stores.data, overlay)

    listed = list_cards(sandboxed, {"customer_id": OWN, "origin_jti": SIGN_IN})

    assert "Blocked" not in {
        c["product_status"]
        for c in listed["cards"]
        if c["card_id"] != "PRD-EXAMPLE00001"
    }
    for card_id in ("PRD-EXAMPLE00002", "PRD-EXAMPLE00005"):
        read = get_card(
            sandboxed, {"customer_id": OWN, "origin_jti": SIGN_IN, "card_id": card_id}
        )
        assert read["card"]["product_status"] == "Active"
