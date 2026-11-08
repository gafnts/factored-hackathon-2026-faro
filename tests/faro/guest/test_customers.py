"""
The guest list reaches the stack and comes back as IDs and synthetic names alone, and a list that names a held-out
customer, or isn't one make guest-list wrote, is refused on both sides (ADR-0007, The guest entry; ADR-0005; SEC-03).
"""

import json

import pytest

from faro.guest.customers import Customer, ListError, decode, encode, parameter
from faro.split import held_out

DEVELOPMENT = [f"CLI-TEAM{n:08d}" for n in (1, 3, 4)]
HELD_OUT = "CLI-TEAM00000002"


def test_the_list_round_trips_in_order() -> None:
    customers = tuple(
        Customer(i, f"Team Customer {n}") for n, i in enumerate(DEVELOPMENT)
    )

    text = encode("snap", customers)

    assert decode(text) == customers
    assert json.loads(text)["snapshot"] == "snap"


def test_the_entry_is_named_under_the_environments_prefix() -> None:
    assert (
        parameter("banking-agent-prototype")
        == "/banking-agent-prototype/guest-entry/customers"
    )


@pytest.mark.parametrize(
    ("customers", "reason"),
    [
        ((), "empty"),
        ((Customer(HELD_OUT, "Team Customer 02"),), "held-out"),
        ((Customer("CLI-TEAM0001", "Team"),), "malformed"),
        ((Customer(DEVELOPMENT[0], " "),), "malformed"),
        ((Customer(DEVELOPMENT[0], "A"), Customer(DEVELOPMENT[0], "B")), "twice"),
    ],
)
def test_a_list_that_cant_be_played_is_refused_both_ways(
    customers: tuple[Customer, ...], reason: str
) -> None:
    assert held_out(HELD_OUT)
    with pytest.raises(ListError, match=reason):
        encode("snap", customers)
    body = {
        "version": 1,
        "snapshot": "snap",
        "customers": [
            {"customer_id": c.customer_id, "name": c.name} for c in customers
        ],
    }
    with pytest.raises(ListError, match=reason):
        decode(json.dumps(body))


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps({"version": 2, "snapshot": "snap", "customers": []}),
        json.dumps({"version": 1, "snapshot": "snap"}),
        json.dumps({"version": 1, "customers": [{"customer_id": 1, "name": "Team"}]}),
    ],
)
def test_a_list_make_guest_list_didnt_write_is_refused(text: str) -> None:
    with pytest.raises(ListError):
        decode(text)


def test_a_list_past_the_standard_tier_is_refused() -> None:
    customers = [Customer(i, "x" * 2000) for i in DEVELOPMENT]

    with pytest.raises(ListError, match="4 KB"):
        encode("snap", customers)
