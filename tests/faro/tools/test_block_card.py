"""
block_card blocks only under a confirmation the control confirmed for this customer, card, and reason, uses it up with
the first write, reads the card back, writes again only after a read-back that doesn't show Blocked, three writes at
most, and never blocks twice under one confirmation (ADR-0004, The confirmation; POL-33 to POL-37; CTL-02, AI-05,
OPS-04, SEC-07).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from faro.contracts import validator
from faro.tools import OutputContractError, block
from faro.tools.block import answer, handler, tool_name
from faro.tools.block_card import block_card
from faro.tools.sandbox import BlockStores, MemorySandbox
from faro.tools.store import MemoryData, Record

from .conftest import OTHER, OWN, SIGN_IN, example_items, overlay_item

NOW = datetime(2026, 10, 2, 15, 41, 37, tzinfo=UTC)
CONFIRMATION = "7c1e2a94-3b5d-4f08-a6e2-9d4b0c8f1e37"
ELSEWHERE = "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50"
CALL = {
    "customer_id": OWN,
    "origin_jti": SIGN_IN,
    "card_id": "PRD-EXAMPLE00002",
    "reason": "lost",
    "confirmation_id": CONFIRMATION,
}


def confirmation(**fields: Any) -> Record:
    return {
        "confirmation_id": CONFIRMATION,
        "thread_key": "11111111-2222-5333-8444-555555555555",
        "sub": "a41c9e27-6b3d-4f58-9e12-7c0d8b5a3f64",
        "origin_jti": SIGN_IN,
        "customer_id": OWN,
        "card_id": "PRD-EXAMPLE00002",
        "reason": "lost",
        "status": "confirmed",
        "created_at": "2026-10-02T15:41:04.702Z",
        "confirmed_at": "2026-10-02T15:41:36.900Z",
        "expires_at": int((NOW + timedelta(minutes=4)).timestamp()),
        "ttl": int((NOW + timedelta(hours=24)).timestamp()),
        **fields,
    }


class LossySandbox(MemorySandbox):
    """
    Loses the first writes to the overlay, as a sandbox that accepted a write it never applied would.
    """

    def __init__(self, lost: int, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lost = lost
        self.writes = 0

    def put(self, item: Any) -> None:
        self.writes += 1
        if self.writes > self.lost:
            super().put(item)


def stores(sandbox: MemorySandbox) -> BlockStores:
    return BlockStores(MemoryData(example_items()), sandbox)


def fits(output: dict[str, Any]) -> bool:
    return validator("tools", "block_card_output").is_valid(output)


def test_a_confirmed_block_uses_the_confirmation_and_is_verified() -> None:
    sandbox = MemorySandbox([confirmation()])

    output = block_card(stores(sandbox), CALL, NOW)

    assert fits(output)
    assert {k: output[k] for k in ("attempts", "block_outcome", "read_back")} == {
        "attempts": 1,
        "block_outcome": "verified",
        "read_back": "Blocked",
    }
    assert output["repeated"] is False
    assert sandbox.status(SIGN_IN, OWN, "PRD-EXAMPLE00002") == "Blocked"
    held = sandbox.confirmations[CONFIRMATION]
    assert (held["status"], held["attempts"], held["outcome"]) == (
        "consumed",
        1,
        "verified",
    )


@pytest.mark.parametrize(
    ("held", "call"),
    [
        (None, {}),
        ({"customer_id": OTHER}, {}),
        ({"status": "pending"}, {}),
        ({"status": "cancelled"}, {}),
        ({"status": "lapsed"}, {}),
        ({"expires_at": int(NOW.timestamp())}, {}),
        ({}, {"card_id": "PRD-EXAMPLE00005"}),
        ({}, {"reason": "stolen"}),
    ],
    ids=[
        "missing",
        "another_customers",
        "pending",
        "cancelled",
        "lapsed",
        "past_its_limit",
        "another_card",
        "another_reason",
    ],
)
def test_a_block_without_a_confirmation_it_can_use_is_refused_alike(
    held: dict[str, Any] | None, call: dict[str, Any]
) -> None:
    sandbox = MemorySandbox([] if held is None else [confirmation(**held)])
    before = [dict(c) for c in sandbox.confirmations.values()]

    output = block_card(stores(sandbox), {**CALL, **call}, NOW)

    assert fits(output)
    assert (output["outcome"], output["refusal"]) == ("refused", "not_confirmed")
    assert "product_status" not in output
    assert sandbox.items == {}
    assert list(sandbox.confirmations.values()) == before


@pytest.mark.parametrize(
    ("card_id", "written", "status"),
    [
        ("PRD-EXAMPLE00002", True, "Blocked"),
        ("PRD-EXAMPLE00007", False, "Closed"),
        ("PRD-EXAMPLE00001", False, "Blocked"),
    ],
)
def test_a_card_that_isnt_active_isnt_blocked_and_keeps_the_confirmation(
    card_id: str, written: bool, status: str
) -> None:
    held = confirmation(card_id=card_id)
    sandbox = MemorySandbox([held], [overlay_item(card_id)] if written else [])
    items = dict(sandbox.items)

    output = block_card(stores(sandbox), {**CALL, "card_id": card_id}, NOW)

    assert fits(output)
    assert output == {
        "outcome": "refused",
        "stamp": output["stamp"],
        "clock": output["clock"],
        "refusal": "not_active",
        "product_status": status,
    }
    assert sandbox.items == items
    assert sandbox.confirmations[CONFIRMATION] == held


@pytest.mark.parametrize(
    ("lost", "attempts", "outcome", "read_back"),
    [
        (1, 2, "verified", "Blocked"),
        (2, 3, "verified", "Blocked"),
        (3, 3, "not_verified", "Active"),
    ],
)
def test_a_write_the_read_back_doesnt_show_is_retried_up_to_three_writes(
    lost: int, attempts: int, outcome: str, read_back: str
) -> None:
    sandbox = LossySandbox(lost, [confirmation()])

    output = block_card(stores(sandbox), CALL, NOW)

    assert fits(output)
    assert (output["attempts"], output["block_outcome"], output["read_back"]) == (
        attempts,
        outcome,
        read_back,
    )
    assert sandbox.writes == attempts
    held = sandbox.confirmations[CONFIRMATION]
    assert (held["attempts"], held["outcome"]) == (attempts, outcome)


def test_a_second_call_writes_nothing_and_reports_the_first_outcome() -> None:
    sandbox = LossySandbox(0, [confirmation()])
    first = block_card(stores(sandbox), CALL, NOW)

    again = block_card(stores(sandbox), CALL, NOW + timedelta(minutes=10))

    assert fits(again)
    assert again == {**first, "repeated": True}
    assert sandbox.writes == 1


def test_a_call_that_stopped_before_its_outcome_is_finished_by_the_next() -> None:
    # The first call used the confirmation, and its write never showed, then it crashed before reading back.
    held = confirmation(
        status="consumed", attempts=1, consumed_at="2026-10-02T15:41:37.100Z"
    )
    sandbox = LossySandbox(0, [held])

    output = block_card(stores(sandbox), CALL, NOW)

    assert (output["attempts"], output["block_outcome"], output["repeated"]) == (
        2,
        "verified",
        False,
    )
    assert sandbox.writes == 1


def test_a_call_that_stopped_after_three_writes_writes_no_more() -> None:
    held = confirmation(status="consumed", attempts=3)
    sandbox = LossySandbox(0, [held])

    output = block_card(stores(sandbox), CALL, NOW)

    assert (output["attempts"], output["block_outcome"]) == (3, "not_verified")
    assert sandbox.writes == 0


def test_the_block_lands_in_the_confirmations_sign_in_whatever_the_call_names() -> None:
    sandbox = MemorySandbox([confirmation(origin_jti=ELSEWHERE)])

    output = block_card(stores(sandbox), CALL, NOW)

    assert output["block_outcome"] == "verified"
    assert sandbox.status(ELSEWHERE, OWN, "PRD-EXAMPLE00002") == "Blocked"
    assert sandbox.status(SIGN_IN, OWN, "PRD-EXAMPLE00002") is None


class RacingSandbox(MemorySandbox):
    """
    Another call uses the confirmation between this call's read and its write.
    """

    def consume(self, confirmation: Record, at: datetime) -> bool:
        assert super().consume(confirmation, at)
        assert self.settle(confirmation["confirmation_id"], 1, "verified", "Blocked")
        return False


def test_two_calls_at_once_block_once() -> None:
    sandbox = RacingSandbox([confirmation()])

    output = block_card(stores(sandbox), CALL, NOW)

    assert (output["block_outcome"], output["repeated"]) == ("verified", True)
    assert len(sandbox.items) == 1


def context(name: str | None) -> Any:
    if name is None:
        return SimpleNamespace(client_context=None)
    return SimpleNamespace(
        client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": name})
    )


def unopened() -> BlockStores:
    raise AssertionError("the stores were opened")


@pytest.mark.parametrize(
    "name", [None, "", "reads___get_card", "reads___list_cards", "block___file_handoff"]
)
def test_a_name_that_isnt_the_block_is_refused(name: str | None) -> None:
    with pytest.raises(ValueError, match="not the block tool"):
        tool_name(context(name))


def test_an_invalid_input_is_answered_without_reading() -> None:
    result = answer(
        {**CALL, "reason": "fraud", "card_id": "4123456789014821"}, unopened
    )

    assert result == {
        "outcome": "invalid_input",
        "errors": [
            {"path": "/card_id", "rule": "pattern"},
            {"path": "/reason", "rule": "enum"},
        ],
    }


def test_the_handler_blocks_under_the_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = MemorySandbox(
        [
            confirmation(
                expires_at=int((datetime.now(UTC) + timedelta(minutes=4)).timestamp())
            )
        ]
    )
    monkeypatch.setattr(block, "stores", lambda: stores(sandbox))

    result = handler(CALL, context("block___block_card"))

    assert (result["outcome"], result["block_outcome"]) == ("ok", "verified")


def test_an_output_that_doesnt_fit_the_contract_is_never_returned() -> None:
    items = example_items()
    items[0]["stamp"] = {"snapshot": "not-a-snapshot"}

    with pytest.raises(OutputContractError, match="block_card's output"):
        answer(
            CALL,
            lambda: BlockStores(MemoryData(items), MemorySandbox([confirmation()])),
            lambda: NOW,
        )
