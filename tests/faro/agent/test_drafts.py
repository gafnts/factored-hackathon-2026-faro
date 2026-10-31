"""
A block whose confirmation's end may require a handoff saves a draft of it when the confirm control shows, through
file_handoff, and the turn that ends the confirmation files that draft: POL-39's however it ends, POL-38's when a lost or
stolen card's block lapses at its time limit or with the sign-in. A cancel or a new request needs none for a lost card
(ADR-0004, The confirmation, decision 7 as amended; ADR-0004's amendments of 2026-09-30; POL-36 to POL-39, POL-45,
POL-47; D1).
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest

from faro.agent.texts import FIXED

from .conftest import Customer, Harness, cards_answer, run_body
from .test_block import THREAD, Chat, control_shown, interrupt, reply

CARD = "tarjeta de crédito terminada en 4821"


def items(harness: Harness, status: str) -> list[dict[str, Any]]:
    return [
        c
        for c in harness.cases.items.values()
        if c["kind"] == "case" and c["status"] == status
    ]


def filed(harness: Harness) -> dict[str, Any]:
    (case,) = items(harness, "filed")
    return case


def handoffs(chat: Chat) -> list[dict[str, Any]]:
    return [e for e in chat.entries() if e["kind"] == "handoff"]


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("lost", "block_lapsed"),
        ("stolen", "block_lapsed"),
        ("unrecognized_charge", "unrecognized_charge"),
        ("other_reason", None),
    ],
)
def test_the_confirm_control_saves_a_draft_when_its_end_may_need_a_person(
    harness: Harness, reason: str, code: str | None
) -> None:
    chat = Chat(harness)

    shown = control_shown(chat, block_reason=reason)

    assert interrupt(shown)
    drafts = items(harness, "draft")
    if code is None:
        assert drafts == [] and handoffs(chat) == []
        return
    (draft,) = drafts
    assert (draft["draft"]["reason_code"], draft["draft"]["reason"]) == (code, reason)
    assert draft["draft"]["sign_in"] == chat.who.origin_jti
    (entry,) = handoffs(chat)
    assert (entry["handoff_id"], entry["status"], entry["priority"]) == (
        draft["handoff_id"],
        "draft_saved",
        "urgent",
    )


def test_a_cancelled_unrecognized_charge_block_files_its_draft_as_urgent(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat, block_reason="unrecognized_charge")
    (draft,) = items(harness, "draft")

    cancelled = chat.press("cancel", shown)

    case = filed(harness)
    assert case["handoff_id"] == draft["handoff_id"]
    assert case["saved_at"] == draft["saved_at"]
    assert reply(cancelled).split("\n\n") == [
        FIXED["confirmation_cancelled"]["es"].format(card=CARD),
        FIXED["handoff_filed"]["es"].format(reference=case["reference"]),
    ]
    payload = case["payload"]
    assert (payload["reason_code"], case["queue"], case["priority"]) == (
        "unrecognized_charge",
        "dispute_intake",
        "urgent",
    )
    (action,) = payload["actions"]
    assert action["outcome"] == "declined_by_customer"
    assert case["flagged"] is False
    assert chat.decision() == {
        "request_label": "block_card",
        "outcome_class": "hand_off",
        "awaiting": "none",
        "rules": ["POL-36", "POL-39", "POL-45"],
    }


def test_a_verified_unrecognized_charge_block_files_its_draft_as_normal(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat, block_reason="unrecognized_charge")

    chat.press("confirm", shown)

    case = filed(harness)
    (action,) = case["payload"]["actions"]
    assert (action["outcome"], case["priority"], case["flagged"]) == (
        "verified",
        "normal",
        False,
    )
    assert items(harness, "draft") == []


def test_a_cancelled_lost_block_files_nothing(harness: Harness) -> None:
    chat = Chat(harness)

    cancelled = chat.press("cancel", control_shown(chat))

    assert reply(cancelled) == FIXED["confirmation_cancelled"]["es"].format(card=CARD)
    assert items(harness, "filed") == []
    assert len(items(harness, "draft")) == 1


def test_a_lost_block_that_lapsed_at_its_time_limit_is_filed_when_the_customer_writes(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    control_shown(chat)
    harness.moved = timedelta(minutes=6)

    late = chat.say("Hola", requests=[])

    case = filed(harness)
    assert reply(late).split("\n\n") == [
        FIXED["confirmation_lapsed"]["es"].format(card=CARD),
        FIXED["handoff_filed"]["es"].format(reference=case["reference"]),
    ]
    (action,) = case["payload"]["actions"]
    assert (case["payload"]["reason_code"], action["outcome"], case["priority"]) == (
        "block_lapsed",
        "lapsed",
        "urgent",
    )
    assert chat.decision()["outcome_class"] == "hand_off"


def test_a_new_request_that_ends_a_charges_confirmation_is_served_after_the_filing(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    control_shown(chat, block_reason="unrecognized_charge")

    moved_on = chat.say(
        "¿Cuáles son mis tarjetas?", requests=["card_status"], cards="all"
    )

    case = filed(harness)
    assert reply(moved_on) == "\n\n".join(
        [
            FIXED["confirmation_lapsed"]["es"].format(card=CARD),
            FIXED["handoff_filed"]["es"].format(reference=case["reference"]),
            cards_answer(harness.bank),
        ]
    )
    assert moved_on[-1]["outcome"] == {"type": "success"}
    # Each request the turn served has its decision, the charge's first (POL-05).
    charge, listed = [e for e in chat.entries() if e["kind"] == "decision"]
    assert (
        charge["request_label"],
        charge["outcome_class"],
        charge["pending_labels"],
    ) == ("block_card", "hand_off", ["card_status"])
    assert {"POL-36", "POL-39", "POL-45"} <= set(charge["rules"])
    assert (listed["request_label"], listed["outcome_class"]) == (
        "card_status",
        "answer",
    )


def test_a_new_request_that_ends_a_lost_cards_confirmation_files_nothing(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    control_shown(chat)

    chat.say("¿Cuáles son mis tarjetas?", requests=["card_status"], cards="all")

    assert items(harness, "filed") == []


def test_a_confirm_from_a_later_sign_in_files_the_lapsed_block_under_it(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat)
    later = Customer(chat.who.sub, str(uuid.uuid4()), chat.who.customer_id)
    control = interrupt(shown)
    answer = {
        "kind": "confirm",
        "confirmation_id": control["metadata"]["controls"][0]["confirmation_id"],
    }
    body = run_body(
        None,
        thread=THREAD,
        resume=[
            {"interruptId": control["id"], "status": "resolved", "payload": answer}
        ],
    )

    harness.post(body, later.token(), chat.session)

    case = filed(harness)
    assert (case["payload"]["reason_code"], case["payload"]["session_id"]) == (
        "block_lapsed",
        later.origin_jti,
    )
    assert case["record"]["sign_in"] == later.origin_jti
    assert case["flagged"] is False


def test_a_draft_that_couldnt_be_saved_is_filed_all_the_same(
    harness: Harness,
) -> None:
    harness.lambda_client.failures = 3
    chat = Chat(harness)
    shown = control_shown(chat, block_reason="unrecognized_charge")
    assert items(harness, "draft") == [] and handoffs(chat) == []

    chat.press("cancel", shown)

    case = filed(harness)
    assert "saved_at" not in case
    assert case["priority"] == "urgent"
