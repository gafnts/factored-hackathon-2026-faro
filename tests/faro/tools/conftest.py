"""
The contract's example items plus a second customer, who is Suspended, and two more cards for the first, all written by
the team, with an empty sandbox.
"""

import json
from importlib.resources import files
from typing import Any

import pytest

from faro.tools.sandbox import MemoryOverlay, Stores
from faro.tools.store import MemoryData

from ..conftest import tools_data_example

OWN = "CLI-EXAMPLE00001"
OTHER = "CLI-EXAMPLE00009"
SIGN_IN = "5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68"


def card(
    customer: str, card_id: str, product_type: str, last_four: str, status: str
) -> dict[str, Any]:
    return {
        "pk": customer,
        "sk": f"CARD#{card_id}",
        "kind": "card",
        "card_id": card_id,
        "product_type": product_type,
        "last_four": last_four,
        "currency": "USD",
        "current_balance": 0,
        "credit_limit": None,
        "product_status": status,
        "opening_date": "2024-01-01",
        "expiration_date": None,
        "past_expiration": False,
        "updated_after_as_of": False,
    }


def example_items() -> list[dict[str, Any]]:
    return [
        *tools_data_example(),
        card(OWN, "PRD-EXAMPLE00007", "Tarjeta Crédito", "9034", "Closed"),
        card(OWN, "PRD-EXAMPLE00005", "Tarjeta Débito", "1177", "Active"),
        # Shares the first card's last four digits, so only the card ID orders them.
        card(OWN, "PRD-EXAMPLE00001", "Tarjeta Crédito", "4821", "Blocked"),
        {
            "pk": OTHER,
            "sk": "CUSTOMER",
            "kind": "customer",
            "customer_id": OTHER,
            "customer_status": "Suspended",
            "country": "Colombia",
            "updated_after_as_of": False,
        },
        card(OTHER, "PRD-EXAMPLE00008", "Tarjeta Débito", "5500", "Active"),
    ]


def overlay_item(
    card_id: str, sign_in: str = SIGN_IN, customer_id: str = OWN
) -> dict[str, Any]:
    return {
        "sign_in": sign_in,
        "item": f"CARD#{card_id}",
        "customer_id": customer_id,
        "card_id": card_id,
        "product_status": "Blocked",
        "reason": "lost",
        "confirmation_id": "7c1e2a94-3b5d-4f08-a6e2-9d4b0c8f1e37",
        "written_at": "2026-10-02T15:41:37.110Z",
        "ttl": 1790000000,
    }


@pytest.fixture
def stores() -> Stores:
    return Stores(MemoryData(example_items()), MemoryOverlay())


def _entry(turn: str, seq: int, kind: str, **fields: Any) -> dict[str, Any]:
    started, turn_id = turn.split("#")
    return {
        "sign_in": SIGN_IN,
        "entry_key": f"{turn}#{seq:04d}",
        "turn_id": turn_id,
        "seq": seq,
        "kind": kind,
        "at": started,
        "source": "demo",
        "expires_at": 1800000000,
        **fields,
    }


def _call(
    turn: str,
    seq: int,
    call_id: str,
    tool: str,
    called_at: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    return _entry(
        turn,
        seq,
        "tool_call",
        call_id=call_id,
        tool=tool,
        via="gateway",
        attempt=1,
        called_at=called_at,
        latency_ms=120,
        request_id=None,
        input={"customer_id": OWN, "origin_jti": SIGN_IN, **arguments},
        outcome=result["outcome"],
        result=result,
    )


def recorded_example() -> list[dict[str, Any]]:
    """
    The tool calls the contract's example handoff cites, as the execution record holds them: the search in one turn, the
    block and the read-back in the next.
    """
    searched, blocked = file_handoff_example()["turns"]
    stamped = {k: tools_data_example()[0][k] for k in ("stamp", "clock")}
    page = contract_examples("tools.find_transactions_output.json")[0]
    block = contract_examples("tools.block_card_output.json")[0]
    card = contract_examples("tools.get_card_output.json")[0]
    confirmation = "7c1e2a94-3b5d-4f08-a6e2-9d4b0c8f1e37"
    card_id = "PRD-EXAMPLE00002"
    return [
        _call(
            searched,
            3,
            "9d1c4b7e-2f3a-4e58-8b6d-1a0c7e5f3b21",
            "find_transactions",
            "2026-10-02T15:40:51Z",
            {"card_id": card_id},
            page,
        ),
        _call(
            blocked,
            2,
            "4e7a2c9d-8b1f-4d36-a5c0-6f2e9b8d1a47",
            "block_card",
            "2026-10-02T15:41:39Z",
            {
                "card_id": card_id,
                "reason": "unrecognized_charge",
                "confirmation_id": confirmation,
            },
            {
                **block,
                **stamped,
                "reason": "unrecognized_charge",
                "confirmation_id": confirmation,
            },
        ),
        _call(
            blocked,
            4,
            "c2b8e1f4-7a9d-4c05-9e3b-5d1a8f6c2e90",
            "get_card",
            "2026-10-02T15:41:40Z",
            {"card_id": card_id},
            card,
        ),
    ]


def contract_examples(name: str) -> list[dict[str, Any]]:
    text = files("faro.contracts").joinpath(f"examples/{name}").read_text()
    loaded: list[dict[str, Any]] = json.loads(text)
    return loaded


def file_handoff_example() -> dict[str, Any]:
    return contract_examples("tools.file_handoff_input.json")[1]
