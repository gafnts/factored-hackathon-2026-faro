"""
A block, end to end through the entrypoint and the real wrapper: which card, a reason, the confirm control, the block
and its read-back, and every way a confirmation ends. Only the control confirms, typed text never does, a stale or
foreign answer changes nothing, and every event stays within the chat's contract (ADR-0004, The confirmation; POL-06,
POL-09, POL-13 to POL-17, POL-31, POL-34 to POL-39; CTL-02, CTL-04, AI-02, AI-05, OPS-02, EVL-03).
"""

import json
import re
from datetime import timedelta
from typing import Any

import pytest

from faro.agent import app as entrypoint
from faro.agent.texts import FIXED
from faro.contracts import validator

from .conftest import (
    Customer,
    Harness,
    cards_answer,
    customer,
    run_body,
    session_id,
)

THREAD = "thread-block-0001"
# What the chat must never receive: the graph's private state, the router's and the extraction's outputs, and the
# tools' identifiers and flags.
PRIVATE = (
    '"case"',
    '"pending"',
    '"asking"',
    "block_reason",
    "card_type",
    "has_request",
    "served_in_full",
    "past_expiration",
    "langgraph",
    "block_card",
    "await_control",
)
# A case reference's four-letter groups can spell a prefix, so the identifiers are matched by their shape.
IDENTIFIER = re.compile(r"(PRD|CLI)-[A-Z0-9]{12}")


def kinds(entries: list[dict[str, Any]]) -> list[str]:
    return [e["kind"] for e in entries]


def turn_of(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last = entries[-1]["turn_id"]
    return [e for e in entries if e["turn_id"] == last]


class Chat:
    """
    One customer's conversation on one thread, checking every event and entry against the contracts.
    """

    def __init__(self, harness: Harness, who: Customer | None = None) -> None:
        self.harness = harness
        self.who = who or customer()
        self.session = session_id()

    def check(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for event in events:
            validator("chat", "event").validate(event)
        sent = json.dumps(events, ensure_ascii=False)
        for private in PRIVATE:
            assert private not in sent, private
        assert not IDENTIFIER.search(sent)
        for entry in self.harness.records.of(self.who.origin_jti):
            validator("execution-record").validate(entry)
        return events

    def say(
        self,
        text: str,
        requests: list[str] | None = None,
        language: str = "unclear",
        **extracted: Any,
    ) -> list[dict[str, Any]]:
        script = self.harness.script
        script.requests = ["block_card"] if requests is None else requests
        script.has_request = bool(script.requests)
        script.language = language
        script.extracted = extracted
        body = run_body(text, thread=THREAD)
        return self.check(self.harness.post(body, self.who.token(), self.session))

    def press(
        self, kind: str, events: list[dict[str, Any]], **payload: Any
    ) -> list[dict[str, Any]]:
        control = interrupt(events)
        answer = {
            "kind": kind,
            "confirmation_id": control["metadata"]["controls"][0]["confirmation_id"],
            **payload,
        }
        entry = {"interruptId": control["id"], "status": "resolved", "payload": answer}
        body = run_body(None, thread=THREAD, resume=[entry])
        return self.check(self.harness.post(body, self.who.token(), self.session))

    def entries(self) -> list[dict[str, Any]]:
        return turn_of(self.harness.records.of(self.who.origin_jti))

    def decision(self) -> dict[str, Any]:
        found = [e for e in self.entries() if e["kind"] == "decision"]
        assert len(found) == 1
        return {
            k: found[0][k]
            for k in ("request_label", "outcome_class", "awaiting", "rules")
        }


def interrupt(events: list[dict[str, Any]]) -> dict[str, Any]:
    outcome = events[-1]["outcome"]
    assert outcome["type"] == "interrupt"
    found: dict[str, Any] = outcome["interrupts"][0]
    return found


def reply(events: list[dict[str, Any]]) -> str:
    text: str = next(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    return text


def called(harness: Harness) -> list[str]:
    return [c["params"]["name"] for c in harness.script.tool_calls]


def control_shown(chat: Chat, **extracted: Any) -> list[dict[str, Any]]:
    extracted = {"card_type": "credit", "block_reason": "lost"} | extracted
    return chat.say("Perdí mi tarjeta de crédito, quiero bloquearla.", **extracted)


def test_a_block_asks_which_card_then_why_then_shows_the_control(
    harness: Harness,
) -> None:
    chat = Chat(harness)

    asked = chat.say("Quiero bloquear mi tarjeta.")

    assert asked[-1]["outcome"] == {"type": "success"}
    assert "4821" in reply(asked) and "1177" in reply(asked)
    assert "9034" not in reply(asked)
    assert chat.decision() == {
        "request_label": "block_card",
        "outcome_class": "clarify",
        "awaiting": "card",
        "rules": ["POL-13", "POL-14"],
    }

    why = chat.say("La de débito.", requests=[], card_type="debit")

    assert "1177" in reply(why)
    assert chat.decision()["awaiting"] == "reason"
    content: Any = harness.script.model_inputs["extract"][1][0].content
    context = content[1]["text"]
    assert "ending in 4821" in context and "ending in 1177" in context

    shown = chat.say("La perdí.", requests=[], block_reason="lost")

    control = interrupt(shown)["metadata"]["controls"][0]
    assert control["card"] == {"type": "Tarjeta Débito", "last_four": "1177"}
    assert control["reason"] == "lost"
    assert reply(shown) == FIXED["confirm_prompt"]["es"].format(
        card="tarjeta de débito terminada en 1177", reason="pérdida"
    )
    assert kinds(chat.entries())[-7:] == [
        "confirmation",
        "tool_call",
        "handoff",
        "reply",
        "interrupt",
        "decision",
        "turn_closed",
    ]
    assert chat.entries()[-1]["outcome"] == "interrupted"
    assert chat.decision() == {
        "request_label": "block_card",
        "outcome_class": "block",
        "awaiting": "confirm_control",
        "rules": ["POL-13", "POL-35", "POL-36"],
    }
    record = harness.confirmations.records[control["confirmation_id"]]
    assert (record["status"], record["card_id"]) == ("pending", "PRD-EXAMPLE00005")
    assert "block___block_card" not in called(harness)


def test_the_control_confirms_and_the_card_is_blocked_and_verified(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat, block_reason="stolen")
    # The card is Active and past its recorded expiration, so the reply states both (POL-31).
    assert reply(shown).startswith(
        FIXED["past_expiration"]["es"].format(
            card="tarjeta de crédito terminada en 4821"
        )
    )
    assert "POL-31" in chat.decision()["rules"]

    done = chat.press("confirm", shown)

    assert [c["kind"] for c in interrupt(done)["metadata"]["controls"]] == [
        "handoff_offer"
    ]
    assert reply(done) == "\n\n".join(
        [
            "Listo: su tarjeta de crédito terminada en 4821 quedó bloqueada.",
            FIXED["replacement_by_person"]["es"],
            FIXED["handoff_offer"]["es"],
        ]
    )
    assert called(harness)[-2:] == ["block___block_card", "reads___get_card"]
    block = harness.script.tool_calls[-2]["params"]["arguments"]
    assert block == {
        "customer_id": chat.who.customer_id,
        "origin_jti": chat.who.origin_jti,
        "card_id": "PRD-EXAMPLE00002",
        "reason": "stolen",
        "confirmation_id": interrupt(shown)["metadata"]["controls"][0][
            "confirmation_id"
        ],
    }
    entries = chat.entries()
    assert kinds(entries) == [
        "turn_opened",
        "resume",
        "confirmation",
        "tool_call",
        "confirmation",
        "tool_call",
        "reply",
        "interrupt",
        "decision",
        "turn_closed",
    ]
    assert entries[0]["input"]["resume"]["kind"] == "confirm"
    assert (entries[1]["accepted"], entries[2]["to"], entries[4]["to"]) == (
        True,
        "confirmed",
        "consumed",
    )
    assert (entries[4]["attempts"], entries[4]["block_outcome"]) == (1, "verified")
    assert chat.decision() == {
        "request_label": "block_card",
        "outcome_class": "block",
        "awaiting": "handoff_control",
        "rules": ["POL-36", "POL-37", "POL-38"],
    }
    assert reply(done).count("\n") == 4


def test_a_typed_yes_leaves_the_confirmation_pending_and_shows_the_control_again(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat)
    first = interrupt(shown)

    again = chat.say("Sí, bloquéela.", requests=["block_card"])

    second = interrupt(again)
    assert second["id"] != first["id"]
    assert second["metadata"] == first["metadata"]
    assert reply(again) == FIXED["control_pointer"]["es"]
    assert "block___block_card" not in called(harness)
    confirmation_id = first["metadata"]["controls"][0]["confirmation_id"]
    assert harness.confirmations.records[confirmation_id]["status"] == "pending"
    entries = chat.entries()
    assert entries[0]["input"] == {
        "kind": "resume",
        "resume": {"kind": "message", "text": "Sí, bloquéela."},
    }
    assert "confirmation" not in kinds(entries)
    assert chat.decision()["rules"] == ["POL-06", "POL-36"]
    held = harness.checkpoint(chat.who, THREAD)["messages"]
    assert [m.content for m in held if m.type == "human"][-1] == "Sí, bloquéela."

    done = chat.press("confirm", again)

    assert "quedó bloqueada" in reply(done)


def test_a_card_number_typed_while_the_control_shows_is_masked(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    control_shown(chat)

    chat.say("Sí, la 4123 4567 8901 4821.", requests=[], last_four="4821")

    typed = chat.entries()[0]["input"]["resume"]["text"]
    assert typed == "Sí, la ****4821."
    held = harness.checkpoint(chat.who, THREAD)["messages"]
    assert "4123" not in json.dumps([m.content for m in held])


def test_the_controls_cancel_ends_the_confirmation_without_blocking(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat)

    cancelled = chat.press("cancel", shown)

    assert (
        reply(cancelled)
        == "De acuerdo: no bloqueé su tarjeta de crédito terminada en 4821."
    )
    assert "block___block_card" not in called(harness)
    assert chat.decision()["outcome_class"] == "answer"
    confirmation = [e for e in chat.entries() if e["kind"] == "confirmation"]
    assert [(e["to"], e["cause"]) for e in confirmation] == [("cancelled", "control")]


def test_a_new_request_typed_while_the_control_shows_ends_the_confirmation(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat)

    listed = chat.say("¿Qué tarjetas tengo?", requests=["card_status"], cards="all")

    assert listed[-1]["outcome"] == {"type": "success"}
    lapsed, answered = reply(listed).split("\n\n", 1)
    assert lapsed == FIXED["confirmation_lapsed"]["es"].format(
        card="tarjeta de crédito terminada en 4821"
    )
    assert answered == cards_answer(harness.bank)
    decision = chat.decision()
    assert (decision["request_label"], decision["outcome_class"]) == (
        "card_status",
        "answer",
    )
    assert "POL-36" in decision["rules"]
    confirmation_id = interrupt(shown)["metadata"]["controls"][0]["confirmation_id"]
    record = harness.confirmations.records[confirmation_id]
    assert (record["status"], record["ended_by"]) == ("lapsed", "message")


def test_another_card_typed_while_the_control_shows_starts_another_confirmation(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat)
    first = interrupt(shown)["metadata"]["controls"][0]

    moved = chat.say("No, la de débito.", requests=["block_card"], card_type="debit")

    second = interrupt(moved)["metadata"]["controls"][0]
    assert second["confirmation_id"] != first["confirmation_id"]
    assert (second["card"]["last_four"], second["reason"]) == ("1177", "lost")
    records = harness.confirmations.records
    assert records[first["confirmation_id"]]["status"] == "lapsed"
    assert records[second["confirmation_id"]]["status"] == "pending"
    assert reply(moved).startswith(FIXED["confirmation_lapsed"]["es"][:20])


def test_another_reason_typed_while_the_control_shows_keeps_the_card(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat)

    moved = chat.say("En realidad me la robaron.", requests=[], block_reason="stolen")

    second = interrupt(moved)["metadata"]["controls"][0]
    assert (second["card"]["last_four"], second["reason"]) == ("4821", "stolen")
    assert (
        second["confirmation_id"]
        != interrupt(shown)["metadata"]["controls"][0]["confirmation_id"]
    )


def test_a_request_with_no_reason_is_asked_why_and_any_other_reason_reaches_the_control(
    harness: Harness,
) -> None:
    chat = Chat(harness)

    asked = chat.say("Bloquee mi tarjeta de crédito.", card_type="credit")

    assert asked[-1]["outcome"] == {"type": "success"}
    assert reply(asked) == FIXED["ask_reason"]["es"].format(
        card="tarjeta de crédito terminada en 4821"
    )
    assert (chat.decision()["outcome_class"], chat.decision()["awaiting"]) == (
        "clarify",
        "reason",
    )

    shown = chat.say("Prefiero no decirlo.", requests=[], block_reason="other_reason")

    control = interrupt(shown)["metadata"]["controls"][0]
    assert control["reason"] == "customer_request"
    assert reply(shown).endswith(
        FIXED["confirm_prompt"]["es"].format(
            card="tarjeta de crédito terminada en 4821", reason="su solicitud"
        )
    )
    # The record keeps what the model said; the confirmation keeps POL-35's code.
    extracted = [
        e
        for e in chat.entries()
        if e["kind"] == "model_call" and e["purpose"] == "extract"
    ]
    assert extracted[-1]["output"]["extracted"]["block_reason"] == "other_reason"
    record = harness.confirmations.records[control["confirmation_id"]]
    assert record["reason"] == "customer_request"


def test_a_reason_the_extraction_also_marks_unanswered_reaches_the_control(
    harness: Harness,
) -> None:
    # D-013: a customer's own decision came back as the reason and as no answer in the same call; the reason is the
    # answer, and the question isn't asked again (POL-06, POL-35).
    chat = Chat(harness)
    chat.say("Bloquee mi tarjeta de crédito.", card_type="credit")

    shown = chat.say(
        "Porque así lo decidí.",
        requests=[],
        block_reason="other_reason",
        question="unanswered",
    )

    control = interrupt(shown)["metadata"]["controls"][0]
    assert control["reason"] == "customer_request"
    assert reply(shown).endswith(
        FIXED["confirm_prompt"]["es"].format(
            card="tarjeta de crédito terminada en 4821", reason="su solicitud"
        )
    )


def test_a_message_with_no_reason_while_the_reason_is_asked_is_asked_again(
    harness: Harness,
) -> None:
    # POL-06: with no reason in the message, the mark decides; the question is asked again and counts toward no limit.
    chat = Chat(harness)
    chat.say("Bloquee mi tarjeta de crédito.", card_type="credit")

    again = chat.say("¿Sigue ahí?", requests=[], question="unanswered")

    assert again[-1]["outcome"] == {"type": "success"}
    decision = chat.decision()
    assert (decision["outcome_class"], decision["awaiting"]) == ("clarify", "reason")
    assert "POL-06" in decision["rules"]


def test_the_same_reason_under_the_models_name_typed_while_the_control_shows_points_to_it(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat, block_reason="other_reason")

    pointed = chat.say(
        "Son motivos personales.", requests=[], block_reason="other_reason"
    )

    assert reply(pointed) == FIXED["control_pointer"]["es"]
    assert (
        interrupt(pointed)["metadata"]["controls"][0]["confirmation_id"]
        == interrupt(shown)["metadata"]["controls"][0]["confirmation_id"]
    )


def test_another_reason_under_the_models_name_typed_while_the_control_shows_moves_the_block(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    control_shown(chat)

    moved = chat.say("Mejor no digo por qué.", requests=[], block_reason="other_reason")

    second = interrupt(moved)["metadata"]["controls"][0]
    assert (second["card"]["last_four"], second["reason"]) == (
        "4821",
        "customer_request",
    )


def test_a_confirm_after_the_time_limit_is_refused_and_nothing_is_blocked(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat)
    harness.moved = timedelta(minutes=5, seconds=1)

    late = chat.press("confirm", shown)

    assert "block___block_card" not in called(harness)
    (filed,) = [e for e in chat.entries() if e["kind"] == "handoff"]
    assert reply(late).split("\n\n") == [
        FIXED["confirmation_lapsed"]["es"].format(
            card="tarjeta de crédito terminada en 4821"
        ),
        FIXED["handoff_filed"]["es"].format(reference=filed["reference"]),
    ]
    entries = chat.entries()
    assert (entries[1]["accepted"], entries[1]["refusal"]) == (False, "expired")
    assert (entries[2]["to"], entries[2]["cause"]) == ("lapsed", "time_limit")
    assert (filed["reason_code"], filed["priority"]) == ("block_lapsed", "urgent")
    assert chat.decision()["outcome_class"] == "hand_off"
    assert chat.decision()["rules"] == ["POL-36", "POL-38", "POL-45"]


def test_a_message_after_the_time_limit_ends_the_confirmation_and_is_served(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    control_shown(chat, block_reason="other_reason")
    harness.moved = timedelta(minutes=6)

    late = chat.say("Sí.", requests=[])

    assert late[-1]["outcome"] == {"type": "success"}
    assert reply(late) == FIXED["confirmation_lapsed"]["es"].format(
        card="tarjeta de crédito terminada en 4821"
    )
    assert chat.decision()["outcome_class"] == "answer"


def test_a_confirm_from_another_sign_in_is_refused_and_ends_the_confirmation(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat)
    later = Customer(
        chat.who.sub, "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50", chat.who.customer_id
    )
    chat.who = later

    refused = chat.press("confirm", shown)

    assert "block___block_card" not in called(harness)
    assert "no la bloqueé" in reply(refused)
    entries = chat.entries()
    assert entries[1]["refusal"] == "other_sign_in"
    assert (entries[2]["to"], entries[2]["cause"]) == ("lapsed", "session_end")
    assert chat.decision()["rules"][0] == "POL-09"


def test_a_resume_that_doesnt_answer_the_pending_control_changes_nothing(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat)
    control = interrupt(shown)
    confirmation_id = control["metadata"]["controls"][0]["confirmation_id"]
    before = harness.checkpoint(chat.who, THREAD)
    stale = [
        (
            {"interruptId": "another-interrupt", "status": "resolved",
             "payload": {"kind": "confirm", "confirmation_id": confirmation_id}},
            "/resume/0/interruptId",
        ),
        (
            {"interruptId": control["id"], "status": "resolved",
             "payload": {"kind": "confirm", "confirmation_id": "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50"}},
            "/resume/0/payload/confirmation_id",
        ),
        (
            {"interruptId": control["id"], "status": "resolved",
             "payload": {"kind": "accept", "offer_id": confirmation_id}},
            "/resume/0/payload/offer_id",
        ),
    ]  # fmt: skip

    for entry, path in stale:
        body = run_body(None, thread=THREAD, resume=[entry])
        events = chat.check(harness.post(body, chat.who.token(), chat.session))

        assert [e["type"] for e in events] == ["RUN_ERROR"]
        refusal = harness.records.of(chat.who.origin_jti)[-1]
        assert refusal["errors"] == [{"path": path, "rule": "notPending"}]

    assert harness.checkpoint(chat.who, THREAD) == before
    assert harness.confirmations.records[confirmation_id]["status"] == "pending"
    assert "quedó bloqueada" in reply(chat.press("confirm", shown))


def test_a_control_answered_once_is_stale_afterwards(harness: Harness) -> None:
    chat = Chat(harness)
    shown = control_shown(chat, block_reason="other_reason")
    chat.press("confirm", shown)
    calls = len(harness.script.tool_calls)

    again = chat.press("confirm", shown)

    assert [e["type"] for e in again] == ["RUN_ERROR"]
    assert harness.records.of(chat.who.origin_jti)[-1]["errors"] == [
        {"path": "/resume", "rule": "notPending"}
    ]
    assert len(harness.script.tool_calls) == calls


def test_a_block_the_read_back_doesnt_show_is_never_reported_as_done(
    harness: Harness,
) -> None:
    harness.bank.verified = False
    chat = Chat(harness)

    done = chat.press("confirm", control_shown(chat))

    (filed,) = [e for e in chat.entries() if e["kind"] == "handoff"]
    assert reply(done).split("\n\n") == [
        FIXED["block_not_verified"]["es"].format(
            card="tarjeta de crédito terminada en 4821"
        ),
        FIXED["handoff_filed"]["es"].format(reference=filed["reference"]),
    ]
    assert "quedó bloqueada" not in reply(done)
    assert chat.decision()["outcome_class"] == "hand_off"
    assert chat.decision()["rules"] == ["POL-37", "POL-45"]


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        ("error", "hand_off"),
        ("denied", "decline"),
    ],
)
def test_a_block_that_fails_or_is_denied_is_never_reported_as_done(
    harness: Harness, answer: str, outcome: str
) -> None:
    from .conftest import denied, tool_error

    chat = Chat(harness)
    shown = control_shown(chat)
    bank = harness.bank.answer

    def fails_the_block(request: Any) -> Any:
        name = json.loads(request.content)["params"]["name"]
        if name == "block___block_card":
            return tool_error() if answer == "error" else denied()
        return bank(request)

    harness.script.gateway = fails_the_block

    done = chat.press("confirm", shown)

    assert "quedó bloqueada" not in reply(done)
    assert chat.decision()["outcome_class"] == outcome
    assert "consumed" not in [e.get("to") for e in chat.entries()]


def test_an_unrecognized_charge_is_blocked_and_the_turn_is_handed_off(
    harness: Harness,
) -> None:
    chat = Chat(harness)

    done = chat.press(
        "confirm", control_shown(chat, block_reason="unrecognized_charge")
    )

    (filed,) = [e for e in chat.entries() if e["kind"] == "handoff"]
    assert reply(done).split("\n\n")[-1] == FIXED["handoff_filed"]["es"].format(
        reference=filed["reference"]
    )
    assert (filed["queue"], filed["priority"]) == ("dispute_intake", "normal")
    assert chat.decision()["outcome_class"] == "hand_off"
    assert chat.decision()["rules"] == ["POL-36", "POL-37", "POL-39", "POL-45"]


@pytest.mark.parametrize(
    ("ending", "cause", "rules"),
    [
        ("time_limit", "time_limit", ["POL-36", "POL-39", "POL-45"]),
        ("other_sign_in", "session_end", ["POL-09", "POL-36", "POL-39", "POL-45"]),
    ],
)
def test_an_unrecognized_charge_block_that_lapses_is_left_for_a_person(
    harness: Harness, ending: str, cause: str, rules: list[str]
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat, block_reason="unrecognized_charge")
    if ending == "time_limit":
        harness.moved = timedelta(minutes=5, seconds=1)
    else:
        chat.who = Customer(
            chat.who.sub, "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50", chat.who.customer_id
        )

    late = chat.press("confirm", shown)

    assert "block___block_card" not in called(harness)
    (filed,) = [e for e in chat.entries() if e["kind"] == "handoff"]
    assert reply(late).split("\n\n") == [
        FIXED["confirmation_lapsed"]["es"].format(
            card="tarjeta de crédito terminada en 4821"
        ),
        FIXED["handoff_filed"]["es"].format(reference=filed["reference"]),
    ]
    confirmation = [e for e in chat.entries() if e["kind"] == "confirmation"]
    assert [(e["to"], e["cause"]) for e in confirmation] == [("lapsed", cause)]
    assert (filed["reason_code"], filed["queue"], filed["priority"]) == (
        "unrecognized_charge",
        "dispute_intake",
        "urgent",
    )
    assert chat.decision() == {
        "request_label": "block_card",
        "outcome_class": "hand_off",
        "awaiting": "none",
        "rules": rules,
    }


@pytest.mark.parametrize("failure", ["read_back", "error"])
def test_an_unrecognized_charge_block_that_isnt_verified_is_left_for_a_person(
    harness: Harness, failure: str
) -> None:
    from .conftest import tool_error

    chat = Chat(harness)
    shown = control_shown(chat, block_reason="unrecognized_charge")
    if failure == "read_back":
        harness.bank.verified = False
    else:
        bank = harness.bank.answer

        def fails_the_block(request: Any) -> Any:
            name = json.loads(request.content)["params"]["name"]
            return tool_error() if name == "block___block_card" else bank(request)

        harness.script.gateway = fails_the_block

    done = chat.press("confirm", shown)

    assert "quedó bloqueada" not in reply(done)
    (filed,) = [e for e in chat.entries() if e["kind"] == "handoff"]
    assert reply(done).endswith(
        FIXED["handoff_filed"]["es"].format(reference=filed["reference"])
    )
    assert (filed["reason_code"], filed["queue"], filed["priority"]) == (
        "unrecognized_charge",
        "dispute_intake",
        "urgent",
    )
    assert chat.decision() == {
        "request_label": "block_card",
        "outcome_class": "hand_off",
        "awaiting": "none",
        "rules": ["POL-37", "POL-39", "POL-45"],
    }


@pytest.mark.parametrize(
    ("last_four", "name"),
    [("9034", "not_blockable"), ("4821", "already_blocked")],
)
def test_a_card_that_isnt_active_gets_no_control(
    harness: Harness, last_four: str, name: str
) -> None:
    harness.bank.blocked.add("PRD-EXAMPLE00002")
    chat = Chat(harness)

    answered = chat.say(
        "Bloqueen mi tarjeta.", last_four=last_four, block_reason="lost"
    )

    assert answered[-1]["outcome"] == {"type": "success"}
    status = "cerrada" if name == "not_blockable" else "bloqueada"
    assert status in reply(answered)
    assert chat.decision()["outcome_class"] == "decline"
    assert chat.decision()["rules"] == ["POL-13", "POL-34"]
    assert harness.confirmations.records == {}


def test_two_questions_that_dont_settle_the_card_stop_the_asking(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    chat.say("Quiero bloquear una tarjeta.")
    chat.say("No sé.", requests=[])
    assert chat.decision()["awaiting"] == "card"

    stopped = chat.say("No recuerdo.", requests=[])

    assert reply(stopped) == "\n\n".join(
        [FIXED["clarification_stopped"]["es"], FIXED["handoff_offer"]["es"]]
    )
    assert chat.decision()["outcome_class"] == "abstain"
    assert "POL-17" in chat.decision()["rules"]
    shown = chat.say(
        "Bloqueen la de débito por robo.", card_type="debit", block_reason="stolen"
    )
    assert interrupt(shown)["metadata"]["controls"][0]["card"]["last_four"] == "1177"


def test_last_four_digits_that_match_no_card_list_the_customers_cards(
    harness: Harness,
) -> None:
    chat = Chat(harness)

    listed = chat.say("Bloqueen la terminada en 5555.", last_four="5555")

    assert all(digits in reply(listed) for digits in ("4821", "9034", "1177"))
    assert chat.decision()["rules"] == ["POL-13", "POL-16"]


@pytest.mark.parametrize(
    ("other_type", "outcome", "rules"),
    [
        ("Tarjeta Débito", "clarify", ["POL-13", "POL-15"]),
        ("Tarjeta Crédito", "hand_off", ["POL-13", "POL-15", "POL-45"]),
    ],
)
def test_last_four_digits_two_active_cards_share_ask_for_the_type_or_hand_off(
    harness: Harness, other_type: str, outcome: str, rules: list[str]
) -> None:
    twin = {
        **harness.bank.listed["cards"][0],
        "card_id": "PRD-EXAMPLE00009",
        "product_type": other_type,
    }
    harness.bank.listed["cards"].append(twin)
    chat = Chat(harness)

    answered = chat.say("Bloqueen la terminada en 4821.", last_four="4821")

    assert chat.decision()["outcome_class"] == outcome
    assert chat.decision()["rules"] == rules
    if outcome == "hand_off":
        (filed,) = [e for e in chat.entries() if e["kind"] == "handoff"]
        assert reply(answered).split("\n\n") == [
            FIXED["ambiguous_card"]["es"].format(last_four="4821"),
            FIXED["handoff_filed"]["es"].format(reference=filed["reference"]),
        ]


def test_a_customer_not_served_in_full_can_still_block(harness: Harness) -> None:
    harness.bank.listed["customer"]["served_in_full"] = False
    chat = Chat(harness)

    shown = control_shown(chat)

    assert interrupt(shown)
    assert "POL-12" in chat.decision()["rules"]


def test_the_control_speaks_the_conversations_language(harness: Harness) -> None:
    chat = Chat(harness)

    shown = chat.say(
        "Perdi meu cartão de crédito, quero bloqueá-lo.",
        language="pt",
        card_type="credit",
        block_reason="lost",
    )

    assert interrupt(shown)["metadata"]["language"] == "pt"
    assert "seu cartão de crédito final 4821 por perda" in reply(shown)


def test_the_entrypoint_records_the_turn_opened_by_a_resume_without_the_message_id(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    control_shown(chat)

    chat.say("sí", requests=[])

    assert "message_id" not in chat.entries()[0]["input"]["resume"]


def test_the_thread_key_binds_the_confirmation(harness: Harness) -> None:
    chat = Chat(harness)
    shown = control_shown(chat)

    confirmation_id = interrupt(shown)["metadata"]["controls"][0]["confirmation_id"]
    record = harness.confirmations.records[confirmation_id]
    assert record["thread_key"] == entrypoint.thread_key(chat.who.sub, THREAD)
    assert (record["sub"], record["origin_jti"]) == (chat.who.sub, chat.who.origin_jti)
