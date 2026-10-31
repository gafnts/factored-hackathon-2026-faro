"""
A handoff the policy requires, end to end through the entrypoint and the real wrapper. Code builds the payload from the
evidence the thread's tool calls left in state, the model writes its free text, and file_handoff's own code files it
under the customer's token, checked against the harness's record. The reply gives the reference in fixed text, every
fact a case states is in the recorded call it cites, and nothing the tool adds reaches the state or the chat (ADR-0004,
The handoff, and its amendments of 2026-09-30; POL-12, POL-15, POL-37, POL-44 to POL-48; CTL-05, AI-05, OPS-02, OPS-05,
SEC-03).
"""

import json
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from faro.agent.texts import FIXED
from faro.policy.handoffs import HANDOFFS
from faro.tools.provenance import holds

from .conftest import Harness, customer
from .test_block import THREAD, Chat, control_shown, reply

TWIN = "PRD-EXAMPLE00009"


def filed(harness: Harness) -> dict[str, Any]:
    cases: list[dict[str, Any]] = [
        c for c in harness.cases.items.values() if c["kind"] == "case"
    ]
    (case,) = cases
    return case


def of_kind(chat: Chat, kind: str) -> list[dict[str, Any]]:
    return [e for e in chat.entries() if e["kind"] == kind]


def filings(chat: Chat) -> list[dict[str, Any]]:
    return [e for e in of_kind(chat, "tool_call") if e["tool"] == "file_handoff"]


def ask_for_a_person(chat: Chat, text: str = "Quiero hablar con una persona.") -> str:
    return reply(chat.say(text, requests=["talk_to_human"]))


def twin(harness: Harness) -> None:
    harness.bank.listed["cards"].append(
        {**harness.bank.listed["cards"][0], "card_id": TWIN}
    )


def test_a_request_for_a_person_is_handed_off_at_once_with_its_reference(
    harness: Harness,
) -> None:
    chat = Chat(harness)

    said = ask_for_a_person(chat)

    case = filed(harness)
    assert said == FIXED["handoff_filed"]["es"].format(reference=case["reference"])
    payload = case["payload"]
    assert (case["status"], case["queue"], case["priority"]) == (
        "filed",
        "customer_service",
        "normal",
    )
    assert (payload["reason_code"], payload["trigger"], payload["rules"]) == (
        "customer_request",
        "required",
        ["POL-44"],
    )
    assert payload["request"]["label"] == "talk_to_human"
    assert (payload["language"], case["source"]) == ("es", "demo")
    assert case["flagged"] is False
    (entry,) = of_kind(chat, "handoff")
    assert {k: entry[k] for k in ("handoff_id", "reference", "status", "queue")} == {
        "handoff_id": case["handoff_id"],
        "reference": case["reference"],
        "status": "filed",
        "queue": "customer_service",
    }
    assert chat.decision() == {
        "request_label": "talk_to_human",
        "outcome_class": "hand_off",
        "awaiting": "none",
        "rules": ["POL-44", "POL-45"],
    }
    (listed,) = [e for e in of_kind(chat, "reply")]
    assert listed["fixed_texts"] == ["handoff_filed"]


def test_a_complaint_is_handed_off_under_its_own_reason(harness: Harness) -> None:
    harness.script.complaint = True
    chat = Chat(harness)

    ask_for_a_person(chat, "Es la tercera vez que me cobran mal, quiero reclamar.")

    payload = filed(harness)["payload"]
    assert (payload["reason_code"], payload["queue"], payload["priority"]) == (
        "complaint",
        "customer_service",
        "normal",
    )


@pytest.mark.parametrize(
    ("label", "priority"),
    [
        ("card_status", "normal"),
        ("available_credit", "normal"),
        ("talk_to_human", "normal"),
        ("unsupported", "normal"),
        ("unrecognized_charge", "urgent"),
    ],
)
def test_a_customer_not_served_in_full_is_handed_off_without_naming_the_status(
    harness: Harness, label: str, priority: str
) -> None:
    harness.bank.listed["customer"]["served_in_full"] = False
    chat = Chat(harness)

    said = reply(chat.say("Necesito ayuda con mi tarjeta.", requests=[label]))

    case = filed(harness)
    assert said == FIXED["handoff_filed"]["es"].format(reference=case["reference"])
    payload = case["payload"]
    assert (payload["reason_code"], payload["request"]["label"]) == (
        "customer_not_active",
        label,
    )
    assert (case["queue"], case["priority"]) == ("customer_service", priority)
    assert chat.decision()["rules"] == ["POL-12", "POL-45"]
    assert harness.script.model_inputs["reply"] == []


def test_two_cards_of_one_type_that_share_the_last_four_are_handed_off(
    harness: Harness,
) -> None:
    twin(harness)
    chat = Chat(harness)

    said = chat.say("Bloqueen la terminada en 4821.", last_four="4821")

    case = filed(harness)
    assert reply(said).split("\n\n") == [
        FIXED["ambiguous_card"]["es"].format(last_four="4821"),
        FIXED["handoff_filed"]["es"].format(reference=case["reference"]),
    ]
    payload = case["payload"]
    assert (payload["reason_code"], payload["request"]["label"]) == (
        "ambiguous_card",
        "block_card",
    )
    cards = {f["id"] for f in payload["verified_facts"] if f["subject"] == "card"}
    assert cards == {"PRD-EXAMPLE00002", TWIN}
    assert case["priority"] == "normal"


def test_a_card_reported_lost_that_cant_be_settled_is_urgent(harness: Harness) -> None:
    twin(harness)
    chat = Chat(harness)

    chat.say(
        "Perdí la terminada en 4821, bloquéenla.", last_four="4821", block_reason="lost"
    )

    assert filed(harness)["priority"] == "urgent"


def test_a_block_the_read_back_doesnt_show_is_handed_off_with_its_outcome(
    harness: Harness,
) -> None:
    harness.bank.verified = False
    chat = Chat(harness)
    shown = control_shown(chat)

    chat.press("confirm", shown)

    case = filed(harness)
    payload = case["payload"]
    calls = {e["tool"]: e for e in of_kind(chat, "tool_call")}
    (action,) = payload["actions"]
    confirmation = harness.confirmations.records[action["confirmation_id"]]
    assert action == {
        "action": "block_card",
        "card_id": "PRD-EXAMPLE00002",
        "reason": "lost",
        "confirmation_id": confirmation["confirmation_id"],
        "outcome": "not_verified",
        "confirmed_at": confirmation["confirmed_at"],
        "evidence": [calls["block_card"]["call_id"]],
    }
    assert (payload["reason_code"], case["priority"]) == (
        "action_not_verified",
        "urgent",
    )
    status = [
        f
        for f in payload["verified_facts"]
        if (f["id"], f["field"]) == ("PRD-EXAMPLE00002", "product_status")
    ]
    assert status == [
        {
            "subject": "card",
            "id": "PRD-EXAMPLE00002",
            "field": "product_status",
            "value": "Active",
            "evidence": calls["get_card"]["call_id"],
        }
    ]
    # The turn that showed the control holds the listing that settled the card; this one, the block and the read.
    turns = sorted(
        {
            e["entry_key"].rsplit("#", 1)[0]
            for e in harness.records.of(chat.who.origin_jti)
            if e["kind"] == "tool_call" and e["tool"] != "file_handoff"
        }
    )
    assert case["record"] == {"sign_in": chat.who.origin_jti, "turns": turns}
    assert len(turns) == 2
    assert case["flagged"] is False


def test_a_block_that_fails_is_handed_off_with_the_failed_call_as_evidence(
    harness: Harness,
) -> None:
    from .conftest import tool_error

    chat = Chat(harness)
    shown = control_shown(chat)
    bank = harness.bank.answer

    def fails_the_block(request: Any) -> Any:
        name = json.loads(request.content)["params"]["name"]
        return tool_error() if name == "block___block_card" else bank(request)

    harness.script.gateway = fails_the_block

    chat.press("confirm", shown)

    case = filed(harness)
    payload = case["payload"]
    assert payload["actions"] == []
    assert {(e["tool"], e["outcome"]) for e in payload["evidence"]} >= {
        ("block_card", "error"),
        ("get_card", "ok"),
    }
    assert (case["priority"], case["flagged"]) == ("urgent", False)


@pytest.mark.parametrize(
    "path",
    ["person", "not_served", "ambiguous", "not_verified"],
)
def test_every_fact_a_case_states_is_in_the_recorded_call_it_cites(
    harness: Harness, path: str
) -> None:
    chat = Chat(harness)
    if path == "person":
        ask_for_a_person(chat)
    if path == "not_served":
        harness.bank.listed["customer"]["served_in_full"] = False
        chat.say("¿Cuáles son mis tarjetas?", requests=["card_status"], cards="all")
    if path == "ambiguous":
        twin(harness)
        chat.say("Bloqueen la terminada en 4821.", last_four="4821")
    if path == "not_verified":
        harness.bank.verified = False
        chat.press("confirm", control_shown(chat))

    case = filed(harness)
    recorded = {
        e["call_id"]: e
        for e in harness.records.of(chat.who.origin_jti)
        if e["kind"] == "tool_call"
    }
    payload = case["payload"]
    assert case["flagged"] is False
    assert payload["verified_facts"]
    for fact in payload["verified_facts"]:
        call = recorded[fact["evidence"]]
        if call["tool"] == "file_handoff":
            assert fact in call["result"]["added_facts"]
        else:
            assert holds(call, fact), fact
    for cited in payload["evidence"]:
        call = recorded[cited["call_id"]]
        assert call["tool"] == cited["tool"]
        # file_handoff states its own call with its own clock, as the provenance check allows.
        if call["tool"] != "file_handoff":
            assert call["called_at"] == cited["called_at"]


def test_the_facts_file_handoff_adds_never_reach_the_state_or_the_chat(
    harness: Harness,
) -> None:
    chat = Chat(harness)

    events = chat.say("Quiero hablar con una persona.", requests=["talk_to_human"])

    payload = filed(harness)["payload"]
    (filing,) = filings(chat)
    added = [f for f in payload["verified_facts"] if f["field"] == "customer_status"]
    assert added == [
        {
            "subject": "customer",
            "id": chat.who.customer_id,
            "field": "customer_status",
            "value": "Active",
            "evidence": filing["call_id"],
        }
    ]
    state = json.dumps(harness.checkpoint(chat.who, THREAD), default=str)
    for private in ("customer_status", "is_fraud", filing["call_id"]):
        assert private not in state
        assert private not in json.dumps(events)


def test_the_model_reads_the_conversation_as_data_and_writes_only_the_free_text(
    harness: Harness,
) -> None:
    harness.script.handoff_text = {
        "summary": "El cliente quiere hablar con una persona sobre su tarjeta.",
        "customer_statements": ["Dice que nadie le responde en la sucursal."],
        "unresolved_questions": ["¿Sobre qué tarjeta quiere hablar?"],
    }
    chat = Chat(harness)

    ask_for_a_person(chat, "Nadie me responde en la sucursal, quiero una persona.")

    (messages,) = harness.script.model_inputs["handoff_text"]
    system, conversation = messages
    assert isinstance(system, SystemMessage) and isinstance(conversation, HumanMessage)
    assert conversation.text == (
        "Customer: Nadie me responde en la sucursal, quiero una persona."
    )
    for private in ("PRD-", "CLI-", "served_in_full", "customer_status"):
        assert private not in json.dumps([m.content for m in messages])
    payload = filed(harness)["payload"]
    assert payload["request"]["summary"] == harness.script.handoff_text["summary"]
    assert payload["customer_statements"] == [
        "Dice que nadie le responde en la sucursal."
    ]
    assert payload["unresolved_questions"] == ["¿Sobre qué tarjeta quiere hablar?"]
    (call,) = [e for e in of_kind(chat, "model_call") if e["node"] == "handoff"]
    assert (call["purpose"], call["outcome"]) == ("handoff_text", "ok")
    assert "output" not in call


def test_text_that_breaks_the_schemas_rules_takes_the_fixed_text(
    harness: Harness,
) -> None:
    harness.script.handoff_text = {
        "summary": "El cliente dio el número 4123 4567 8901 4821.",
        "customer_statements": [
            "Su tarjeta es la 4123456789014821.",
            "x" * 281,
            *[f"Dice algo más, parte {n}." for n in range(1, 8)],
        ],
        "unresolved_questions": [""],
    }
    chat = Chat(harness)

    ask_for_a_person(chat)

    payload = filed(harness)["payload"]
    assert payload["request"]["summary"] == HANDOFFS["customer_request"].summary
    assert payload["customer_statements"] == [
        f"Dice algo más, parte {n}." for n in range(1, 6)
    ]
    assert payload["unresolved_questions"] == []
    assert filed(harness)["flagged"] is False


def test_a_failed_handoff_text_call_files_the_case_with_the_fixed_text(
    harness: Harness,
) -> None:
    harness.script.handoff_error = RuntimeError("provider down")
    chat = Chat(harness)

    said = ask_for_a_person(chat)

    payload = filed(harness)["payload"]
    assert payload["request"]["summary"] == HANDOFFS["customer_request"].summary
    assert (payload["customer_statements"], payload["unresolved_questions"]) == ([], [])
    assert said.startswith(FIXED["handoff_filed"]["es"].split("{")[0])
    (call,) = [e for e in of_kind(chat, "model_call") if e["node"] == "handoff"]
    assert call["outcome"] == "error"


def test_a_filing_that_fails_three_times_says_so_and_files_nothing(
    harness: Harness,
) -> None:
    harness.lambda_client.failures = 3
    chat = Chat(harness)

    said = ask_for_a_person(chat)

    assert said == FIXED["handoff_failed"]["es"]
    assert harness.cases.items == {}
    assert of_kind(chat, "handoff") == []
    assert [(e["attempt"], e["outcome"], e["via"]) for e in filings(chat)] == [
        (1, "failed", "direct"),
        (2, "failed", "direct"),
        (3, "failed", "direct"),
    ]
    assert chat.decision()["outcome_class"] == "abstain"
    assert chat.decision()["rules"] == ["POL-44", "POL-48"]


def test_a_filing_that_fails_once_is_filed_once_under_the_same_call(
    harness: Harness,
) -> None:
    harness.lambda_client.failures = 1
    chat = Chat(harness)

    said = ask_for_a_person(chat)

    case = filed(harness)
    assert said == FIXED["handoff_filed"]["es"].format(reference=case["reference"])
    first, second = filings(chat)
    assert first["call_id"] == second["call_id"]
    assert (first["outcome"], second["outcome"]) == ("failed", "ok")


def test_the_token_reaches_file_handoff_and_never_the_record(harness: Harness) -> None:
    who = customer()
    chat = Chat(harness, who)

    ask_for_a_person(chat)

    (event,) = harness.lambda_client.events
    assert event["token"] == who.token()
    assert event["input"]["mode"] == "file"
    recorded = json.dumps(harness.records.of(who.origin_jti))
    assert who.token() not in recorded
    (filing,) = filings(chat)
    assert filing["input"] == {"handoff_id": filed(harness)["handoff_id"]}


def test_the_turn_records_the_handoffs_prompt_version(harness: Harness) -> None:
    chat = Chat(harness)

    ask_for_a_person(chat)

    (opened,) = of_kind(chat, "turn_opened")
    assert "handoff" in opened["versions"]["prompts"]
