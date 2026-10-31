"""
The handoff's payload as the graph builds it: facts only from the calls it cites and only about the records it names,
the latest read of each field, never a fact file_handoff alone may state, the queue and priority POL-47 gives, and the
model's text only where it passes the schema's rules (ADR-0004, The handoff; POL-11, POL-46, POL-47; CTL-05, OPS-05).
"""

from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from langchain_core.messages import AIMessage, HumanMessage

from faro.agent.models import HandoffText
from faro.agent.payload import (
    DONE,
    FACTS,
    FIELDS,
    NOTHING_DONE,
    READ,
    built,
    context,
    done,
    facts_of,
    required,
    transcript,
    written,
)
from faro.policy import handoff_schema
from faro.policy.handoffs import HANDOFFS

CUSTOMER = "CLI-EXAMPLE00001"
CARD = "PRD-EXAMPLE00002"
OTHER = "PRD-EXAMPLE00005"
SCHEMA = Draft202012Validator(handoff_schema(), format_checker=FormatChecker())


def call(call_id: str, result: Any, tool: str = "list_cards") -> dict[str, Any]:
    return {
        "call_id": call_id,
        "tool": tool,
        "called_at": "2026-10-02T15:40:47.301Z",
        "outcome": "ok",
        "turn": "2026-10-02T15:40:47.000Z#3f6b2d8e-1a4c-4b7e-9d2f-6c8a0e5b3d71",
        "result": result,
    }


def listed(*cards: dict[str, Any]) -> dict[str, Any]:
    return {
        "outcome": "ok",
        "customer": {"served_in_full": True, "country": "México"},
        "cards": list(cards),
    }


def card(card_id: str = CARD, status: str = "Active") -> dict[str, Any]:
    return {
        "card_id": card_id,
        "product_type": "Tarjeta Crédito",
        "last_four": "4821",
        "product_status": status,
        "past_expiration": False,
        "updated_after_as_of": False,
    }


def payload_of(request: dict[str, Any], cited: list[dict[str, Any]]) -> dict[str, Any]:
    return built(
        request,
        cited,
        handoff_id="0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50",
        created_at="2026-10-02T15:42:08.512Z",
        customer_id=CUSTOMER,
        session_id="5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68",
        versions={"policy": 1, "snapshot": "b3b8b248f604ef9a"},
        business_date="2026-06-17",
        language="pt",
    )


def test_the_graph_states_no_fact_only_file_handoff_may_add() -> None:
    assert FIELDS["customer"] == ("country",)
    assert "is_fraud" not in FIELDS["transaction"]
    assert "product_status" in FIELDS["card"]


def test_facts_are_the_named_cards_fields_each_from_its_latest_read() -> None:
    first = call("call-1", listed(card(), card(OTHER)))
    again = call(
        "call-2",
        {"outcome": "ok", "card": {**card(), "product_status": "Blocked"}},
        "get_card",
    )

    facts = facts_of([first, again], CUSTOMER, [CARD])

    assert {(f["id"], f["field"]) for f in facts} == {
        (CUSTOMER, "country"),
        *((CARD, field) for field in FIELDS["card"] if field in card()),
    }
    (status,) = [f for f in facts if f["field"] == "product_status"]
    assert (status["value"], status["evidence"]) == ("Blocked", "call-2")


def test_a_named_transaction_states_its_fields_and_its_cards_id() -> None:
    window = call(
        "call-4",
        {
            "outcome": "ok",
            "card_id": CARD,
            "transactions": [
                {
                    "transaction_id": "TRX-EXAMPLE0000000000003",
                    "amount": 189.9,
                    "currency": "USD",
                    "response_meaning": None,
                },
                {"transaction_id": "TRX-EXAMPLE0000000000002", "amount": 1200},
            ],
        },
        "find_transactions",
    )

    facts = facts_of([window], CUSTOMER, [], ["TRX-EXAMPLE0000000000003"])

    assert {(f["id"], f["field"], f["value"]) for f in facts} == {
        ("TRX-EXAMPLE0000000000003", "amount", 189.9),
        ("TRX-EXAMPLE0000000000003", "currency", "USD"),
        ("TRX-EXAMPLE0000000000003", "product_id", CARD),
    }


def test_a_block_states_its_read_back_as_the_cards_status() -> None:
    block = call(
        "call-3",
        {
            "outcome": "ok",
            "card_id": CARD,
            "reason": "lost",
            "confirmation_id": "7c1e2a94-3b5d-4f08-a6e2-9d4b0c8f1e37",
            "block_outcome": "not_verified",
            "read_back": "Active",
        },
        "block_card",
    )

    assert facts_of([block], CUSTOMER, [CARD]) == [
        {
            "subject": "card",
            "id": CARD,
            "field": "product_status",
            "value": "Active",
            "evidence": "call-3",
        }
    ]


def test_facts_leave_room_for_what_file_handoff_adds() -> None:
    many = [card(f"PRD-EXAMPLE{n:05d}") for n in range(20)]

    facts = facts_of(
        [call("call-1", listed(*many))], CUSTOMER, [c["card_id"] for c in many]
    )

    assert len(facts) == FACTS


def test_a_failed_call_states_no_fact() -> None:
    failed = {**call("call-1", None), "outcome": "error"}

    assert facts_of([failed], CUSTOMER, [CARD]) == []


@pytest.mark.parametrize(
    ("code", "reported", "actions", "priority"),
    [
        ("customer_request", None, [], "normal"),
        ("ambiguous_card", "lost", [], "urgent"),
        ("ambiguous_card", "customer_request", [], "normal"),
        ("action_not_verified", "stolen", ["not_verified"], "urgent"),
        ("action_not_verified", "lost", ["verified"], "normal"),
    ],
)
def test_the_priority_follows_pol_47(
    code: str, reported: str | None, actions: list[str], priority: str
) -> None:
    request = required(
        code,
        "block_card",
        [],
        calls=["call-1"],
        cards=[CARD],
        actions=[
            {
                "action": "block_card",
                "card_id": CARD,
                "reason": reported,
                "confirmation_id": "7c1e2a94-3b5d-4f08-a6e2-9d4b0c8f1e37",
                "outcome": outcome,
                "confirmed_at": "2026-10-02T15:41:37.102Z",
                "evidence": ["call-1"],
            }
            for outcome in actions
        ],
        reported=reported,
    )

    payload = payload_of(request, [call("call-1", listed(card()))])

    assert payload["priority"] == priority
    assert payload["queue"] == HANDOFFS[code].queue


def test_a_customer_not_served_who_reports_a_charge_is_urgent() -> None:
    request = required(
        "customer_not_active",
        "unrecognized_charge",
        ["POL-12"],
        calls=["call-1"],
        cards=[CARD],
    )

    payload = payload_of(request, [call("call-1", listed(card()))])

    assert payload["priority"] == "urgent"


def test_a_built_payload_fits_the_handoff_schema_with_the_models_text() -> None:
    request = required(
        "customer_request", "talk_to_human", ["POL-44"], calls=["call-1"], cards=[CARD]
    )
    text = HandoffText(
        summary="O cliente pediu uma pessoa.",
        customer_statements=["Quer falar com alguém."],
        unresolved_questions=[],
    )

    payload = written(payload_of(request, [call("call-1", listed(card()))]), text)

    assert list(SCHEMA.iter_errors(payload)) == []
    assert payload["evidence"] == [
        {
            "call_id": "call-1",
            "tool": "list_cards",
            "called_at": "2026-10-02T15:40:47.301Z",
            "outcome": "ok",
        }
    ]


def test_text_the_model_couldnt_write_keeps_the_fixed_text() -> None:
    request = required("tool_failure", "card_status", [])
    payload = payload_of(request, [])

    assert written(payload, None) == payload
    assert payload["request"]["summary"] == HANDOFFS["tool_failure"].summary


def test_a_digit_run_the_model_writes_never_reaches_the_payload() -> None:
    payload = payload_of(required("customer_request", "talk_to_human", []), [])
    text = HandoffText(
        summary="Tarjeta ４１２３４５６７８９０１４８２１.",
        customer_statements=["Número 4123-4567-8901-4821.", " Lo perdió ayer. "],
        unresolved_questions=["x" * 281],
    )

    mended = written(payload, text)

    assert mended["request"]["summary"] == HANDOFFS["customer_request"].summary
    assert mended["customer_statements"] == ["Lo perdió ayer."]
    assert mended["unresolved_questions"] == []


@pytest.mark.parametrize(
    "outcome", ["verified", "not_verified", "declined_by_customer", "lapsed"]
)
def test_the_model_reads_what_the_chat_did_about_the_card_after_the_context(
    outcome: str,
) -> None:
    action: dict[str, Any] = {
        "action": "block_card",
        "card_id": CARD,
        "reason": "unrecognized_charge",
        "confirmation_id": "7c1e2a94-3b5d-4f08-a6e2-9d4b0c8f1e37",
        "outcome": outcome,
        "confirmed_at": None,
        "evidence": [],
    }
    acted = required("unrecognized_charge", "unrecognized_charge", [], actions=[action])
    idle = required("customer_request", "talk_to_human", [])

    # The context keeps its one line, which the deterministic baseline reads as the summary.
    assert context(acted) == (
        f"Why the case goes to a person: {HANDOFFS['unrecognized_charge'].summary}"
    )
    assert done(acted) == DONE[outcome]
    assert done(idle) == NOTHING_DONE
    for line in (*DONE.values(), NOTHING_DONE):
        assert not any(c.isdigit() for c in line)


def test_the_transcript_is_the_latest_messages_labeled_by_speaker() -> None:
    messages = [
        HumanMessage(f"mensaje {n}") if n % 2 else AIMessage(f"respuesta {n}")
        for n in range(READ + 3)
    ]

    lines = transcript(messages).split("\n\n")

    assert len(lines) == READ
    assert lines[-1] == f"Chat: respuesta {READ + 2}"
    assert lines[-2] == f"Customer: mensaje {READ + 1}"
