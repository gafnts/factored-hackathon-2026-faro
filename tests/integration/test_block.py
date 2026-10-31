"""
The confirmation and the verified block on the deployed stack: a persona asks to block one of their Active cards, gives
a reason, confirms with the control, and reads in fixed text that the card is blocked, with the read-back showing it
in that sign-in only, the handoff control offering the replacement, and the whole path in the execution record. A
typed yes confirms nothing and a stale control changes nothing; another sign-in's control and the time limit each end
the confirmation unused, and file the lost card's case; and block_card called through the Gateway without a
confirmation blocks nothing (ADR-0004, The confirmation; POL-09, POL-33 to POL-38; CTL-02, CTL-04, AI-05, SEC-05,
SEC-07, EVL-03, EVL-04, OPS-02). Each test deletes the drafts and cases its conversations leave. Assertions compare
without printing a card, a reply, or an ID.
"""

import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import boto3
import pytest

from faro.agent.texts import FIXED, render
from faro.contracts import validator

from .conftest import SignIn, User, claims, handoffs
from .test_agent import body, post, records, session_id
from .test_stack import DENIED, arguments, call, mcp, tool_output

pytestmark = pytest.mark.integration

ASK = {
    "es": "Quiero bloquear mi {type} terminada en {last_four}.",
    "pt": "Quero bloquear meu {type} final {last_four}.",
}
LOST = {
    "es": "Quiero bloquear mi {type} terminada en {last_four} porque la perdí.",
    "pt": "Perdi meu {type} final {last_four} e quero bloqueá-lo.",
}
TYPES = {
    "es": {
        "Tarjeta Crédito": "tarjeta de crédito",
        "Tarjeta Débito": "tarjeta de débito",
    },
    "pt": {
        "Tarjeta Crédito": "cartão de crédito",
        "Tarjeta Débito": "cartão de débito",
    },
}


@dataclass
class Conversation:
    outputs: dict[str, Any]
    access: str
    language: str
    session: str = field(default_factory=session_id)
    thread: str = field(default_factory=lambda: f"thread-{uuid.uuid4().hex[:12]}")

    @property
    def sign_in(self) -> str:
        found: str = claims(self.access)["origin_jti"]
        return found

    def send(self, sent: dict[str, Any]) -> list[dict[str, Any]]:
        events = post(self.outputs, self.access, sent, self.session)
        for event in events:
            validator("chat", "event").validate(event)
        return events

    def say(self, text: str) -> list[dict[str, Any]]:
        return self.send(body(text, thread=self.thread))

    def press(self, kind: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        shown = interrupt(events)
        answer = {
            "kind": kind,
            "confirmation_id": control_of(events)["confirmation_id"],
        }
        entry = {"interruptId": shown["id"], "status": "resolved", "payload": answer}
        return self.send(body(None, thread=self.thread, resume=[entry]))

    def entries(self) -> list[dict[str, Any]]:
        found = records(self.outputs, self.sign_in)
        for entry in found:
            validator("execution-record").validate(entry)
        return found

    def last_turn(self) -> list[dict[str, Any]]:
        found = self.entries()
        return [e for e in found if e["turn_id"] == found[-1]["turn_id"]]

    def decision(self) -> dict[str, Any]:
        found = next(e for e in self.last_turn() if e["kind"] == "decision")
        return {
            k: found[k] for k in ("request_label", "outcome_class", "awaiting", "rules")
        }


def interrupt(events: list[dict[str, Any]]) -> dict[str, Any]:
    outcome = events[-1]["outcome"]
    assert outcome["type"] == "interrupt", "the run didn't end at the control"
    found: dict[str, Any] = outcome["interrupts"][0]
    return found


def control_of(events: list[dict[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = interrupt(events)["metadata"]["controls"][0]
    return found


def reply(events: list[dict[str, Any]]) -> str:
    text: str = next(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    return text


def active_card(outputs: dict[str, Any], access: str) -> dict[str, Any]:
    listed = tool_output(call(outputs, access, "list_cards", arguments(access)))
    card: dict[str, Any] = next(
        c
        for c in listed["cards"]
        if c["product_status"] == "Active" and not c["past_expiration"]
    )
    return card


def status(outputs: dict[str, Any], access: str, card_id: str) -> str:
    read = tool_output(
        call(outputs, access, "get_card", arguments(access, card_id=card_id))
    )
    found: str = read["card"]["product_status"]
    return found


def asked(language: str, card: dict[str, Any], template: dict[str, str]) -> str:
    return template[language].format(
        type=TYPES[language][card["product_type"]], last_four=card["last_four"]
    )


def confirmation(outputs: dict[str, Any], confirmation_id: str) -> dict[str, Any]:
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(
        outputs["sandbox_tables"]["confirmations"]
    )
    item: dict[str, Any] = table.get_item(
        Key={"confirmation_id": confirmation_id}, ConsistentRead=True
    )["Item"]
    return item


def block_card(
    outputs: dict[str, Any],
    access: str,
    card_id: str,
    confirmation_id: str,
    **overrides: str,
) -> dict[str, Any]:
    return call(
        outputs,
        access,
        "block_card",
        arguments(
            access,
            card_id=card_id,
            reason="lost",
            confirmation_id=confirmation_id,
            **overrides,
        ),
    )


@pytest.fixture
def persona(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn, saved: list[str]
) -> Iterator[Any]:
    started: list[Conversation] = []

    def start(role: str = "customer") -> tuple[Conversation, dict[str, Any]]:
        language = "es" if role == "customer" else "pt"
        access = sign_in(users[role], "customer")["access"]
        started.append(Conversation(outputs, access, language))
        return started[-1], active_card(outputs, access)

    yield start
    for chat in started:
        saved.extend(handoffs(chat.entries()))


def test_a_persona_blocks_a_card_with_the_control_and_reads_it_back(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn, persona: Any
) -> None:
    chat, card = persona()

    why = chat.say(asked("es", card, ASK))

    assert why[-1]["outcome"] == {"type": "success"}
    assert chat.decision()["awaiting"] == "reason"

    shown = chat.say("La perdí.")

    control = control_of(shown)
    named = control["card"] == {
        "type": card["product_type"],
        "last_four": card["last_four"],
    }
    assert named, "the control doesn't name the card"
    assert control["reason"] == "lost"
    assert chat.decision() == {
        "request_label": "block_card",
        "outcome_class": "block",
        "awaiting": "confirm_control",
        "rules": ["POL-13", "POL-35", "POL-36"],
    }
    assert status(outputs, chat.access, card["card_id"]) == "Active"

    done = chat.press("confirm", shown)

    # A replacement is a person's to arrange, so the handoff control offers it (POL-38).
    offer = control_of(done)
    assert (offer["kind"], offer["reason_code"]) == (
        "handoff_offer",
        "unsupported_request",
    )
    facts = {"card": {**card, "product_status": "Blocked"}}
    said = reply(done) == "\n\n".join(
        [
            render("block_verified", "es", facts),
            FIXED["replacement_by_person"]["es"],
            FIXED["handoff_offer"]["es"],
        ]
    )
    assert said, "the reply isn't the verified block's with the offer"
    assert status(outputs, chat.access, card["card_id"]) == "Blocked"
    turn = chat.last_turn()
    assert [e["kind"] for e in turn] == [
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
    blocked = turn[3]
    assert (blocked["tool"], blocked["via"], blocked["outcome"]) == (
        "block_card",
        "gateway",
        "ok",
    )
    assert blocked["result"]["block_outcome"] == "verified"
    assert (turn[4]["to"], turn[4]["attempts"], turn[4]["block_outcome"]) == (
        "consumed",
        1,
        "verified",
    )
    assert chat.decision() == {
        "request_label": "block_card",
        "outcome_class": "block",
        "awaiting": "handoff_control",
        "rules": ["POL-36", "POL-37", "POL-38"],
    }
    record = confirmation(outputs, control["confirmation_id"])
    assert (record["status"], record["outcome"], int(record["attempts"])) == (
        "consumed",
        "verified",
        1,
    )
    # The sandbox belongs to the sign-in: a new one starts from the bank's own records (POL-33).
    later = sign_in(users["customer"], "customer")["access"]
    assert status(outputs, later, card["card_id"]) == "Active"


def test_a_typed_yes_shows_the_control_again_and_the_cancel_ends_it(
    outputs: dict[str, Any], persona: Any
) -> None:
    chat, card = persona("other_customer")
    shown = chat.say(asked("pt", card, LOST))
    first = interrupt(shown)

    again = chat.say("Sim, pode bloquear.")

    second = interrupt(again)
    assert second["id"] != first["id"]
    assert control_of(again) == control_of(shown)
    assert reply(again) == FIXED["control_pointer"]["pt"]
    turn = chat.last_turn()
    assert turn[0]["input"] == {
        "kind": "resume",
        "resume": {"kind": "message", "text": "Sim, pode bloquear."},
    }
    assert "confirmation" not in [e["kind"] for e in turn]
    assert chat.decision()["awaiting"] == "confirm_control"
    assert status(outputs, chat.access, card["card_id"]) == "Active"

    cancelled = chat.press("cancel", again)

    assert cancelled[-1]["outcome"] == {"type": "success"}
    assert "não bloqueei" in reply(cancelled)
    assert (
        confirmation(outputs, control_of(shown)["confirmation_id"])["status"]
        == "cancelled"
    )
    assert status(outputs, chat.access, card["card_id"]) == "Active"


def test_a_stale_control_changes_nothing(outputs: dict[str, Any], persona: Any) -> None:
    chat, card = persona()
    shown = chat.say(asked("es", card, LOST))
    chat.press("confirm", shown)
    used = control_of(shown)["confirmation_id"]

    again = chat.press("confirm", shown)

    assert [e["type"] for e in again] == ["RUN_ERROR"]
    assert again[0]["code"] == "invalid_request"
    refused = chat.entries()[-1]
    assert refused["kind"] == "request_refused"
    # What's pending now is the replacement's offer, not the used control (POL-38).
    assert refused["errors"] == [
        {"path": "/resume/0/interruptId", "rule": "notPending"}
    ]
    record = confirmation(outputs, used)
    assert (record["status"], int(record["attempts"])) == ("consumed", 1)
    assert status(outputs, chat.access, card["card_id"]) == "Blocked"


def test_another_sign_ins_confirmation_is_refused_and_ends(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn, persona: Any
) -> None:
    chat, card = persona()
    shown = chat.say(asked("es", card, LOST))
    control = control_of(shown)
    later = sign_in(users["customer"], "customer")["access"]

    unconfirmed = block_card(
        outputs, later, card["card_id"], control["confirmation_id"]
    )
    assert tool_output(unconfirmed)["refusal"] == "not_confirmed"
    foreign = block_card(
        outputs,
        later,
        card["card_id"],
        control["confirmation_id"],
        origin_jti=chat.sign_in,
    )
    assert foreign["error"]["code"] == DENIED

    elsewhere = Conversation(outputs, later, "es", chat.session, chat.thread)
    refused = elsewhere.press("confirm", shown)

    assert refused[-1]["outcome"] == {"type": "success"}
    assert "no la bloqueé" in reply(refused)
    resume = next(e for e in elsewhere.last_turn() if e["kind"] == "resume")
    assert (resume["accepted"], resume["refusal"]) == (False, "other_sign_in")
    # The customer left a lost card unblocked with the sign-in, so its case goes to a person (POL-38).
    filed = [e for e in elsewhere.last_turn() if e["kind"] == "handoff"]
    assert [(e["reason_code"], e["status"]) for e in filed] == [
        ("block_lapsed", "filed")
    ]
    record = confirmation(outputs, control["confirmation_id"])
    assert (record["status"], record["ended_by"]) == ("lapsed", "session_end")
    assert status(outputs, chat.access, card["card_id"]) == "Active"
    assert status(outputs, later, card["card_id"]) == "Active"


def test_a_confirmation_past_its_time_limit_lapses_unused(
    outputs: dict[str, Any], persona: Any
) -> None:
    chat, card = persona("other_customer")
    shown = chat.say(asked("pt", card, LOST))
    control = control_of(shown)
    # Five minutes pass, as a harness action: the record's own limit moves into the past.
    boto3.resource("dynamodb", region_name="us-east-1").Table(
        outputs["sandbox_tables"]["confirmations"]
    ).update_item(
        Key={"confirmation_id": control["confirmation_id"]},
        UpdateExpression="SET expires_at = :past",
        ExpressionAttributeValues={":past": int(time.time()) - 1},
    )

    late = chat.press("confirm", shown)

    assert late[-1]["outcome"] == {"type": "success"}
    # A lost card left unblocked at the time limit goes to a person (POL-38).
    filed = next(e for e in chat.last_turn() if e["kind"] == "handoff")
    assert (filed["reason_code"], filed["status"]) == ("block_lapsed", "filed")
    assert reply(late).split("\n\n") == [
        render("confirmation_lapsed", "pt", {"card": card}),
        render("handoff_filed", "pt", {"reference": filed["reference"]}),
    ]
    resume = next(e for e in chat.last_turn() if e["kind"] == "resume")
    assert resume["refusal"] == "expired"
    record = confirmation(outputs, control["confirmation_id"])
    assert (record["status"], record["ended_by"]) == ("lapsed", "time_limit")
    assert status(outputs, chat.access, card["card_id"]) == "Active"


def test_block_card_through_the_gateway_blocks_nothing_without_a_confirmation(
    outputs: dict[str, Any], persona: Any
) -> None:
    chat, card = persona()
    shown = chat.say(asked("es", card, LOST))
    pending = control_of(shown)["confirmation_id"]

    for confirmation_id in (str(uuid.uuid4()), pending):
        output = tool_output(
            block_card(outputs, chat.access, card["card_id"], confirmation_id)
        )
        assert validator("tools", "block_card_output").is_valid(output)
        assert (output["outcome"], output["refusal"]) == ("refused", "not_confirmed")
    theirs = block_card(
        outputs, chat.access, card["card_id"], pending, customer_id="CLI-ITEST0000404"
    )
    assert theirs["error"]["code"] == DENIED
    status_code, misnamed = mcp(
        outputs,
        chat.access,
        "tools/call",
        {"name": "reads___block_card", "arguments": arguments(chat.access)},
    )
    assert status_code == 200
    assert "result" not in misnamed or misnamed["result"].get("isError")
    assert status(outputs, chat.access, card["card_id"]) == "Active"
    assert confirmation(outputs, pending)["status"] == "pending"
