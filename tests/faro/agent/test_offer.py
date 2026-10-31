"""
An offered handoff, end to end through the entrypoint and the real wrapper. The handoff control shows after the reply
that offers it, alone or beside a pending confirmation; only the control accepts it, and accepting files it as an
accepted offer, ending a confirmation beside it unused. Typed text points to the control, a new request ends the offer,
and an offer lapses with the session (ADR-0004, The graph, Offered handoffs; POL-06, POL-09, POL-17, POL-36, POL-38,
POL-44, POL-45, POL-48; CTL-03, CTL-05).
"""

import json
import uuid
from typing import Any

from faro.agent.texts import FIXED

from .conftest import Customer, Harness, cards_answer, run_body, tool_error
from .test_block import THREAD, Chat, control_shown, interrupt, reply


def shown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = interrupt(events)["metadata"]["controls"]
    return controls


def offer_of(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(c for c in shown(events) if c["kind"] == "handoff_offer")


def answer_offer(
    chat: Chat, kind: str, events: list[dict[str, Any]], who: Customer | None = None
) -> list[dict[str, Any]]:
    control = interrupt(events)
    answer = {"kind": kind, "offer_id": offer_of(events)["offer_id"]}
    entry = {"interruptId": control["id"], "status": "resolved", "payload": answer}
    body = run_body(None, thread=THREAD, resume=[entry])
    token = (who or chat.who).token()
    return chat.check(chat.harness.post(body, token, chat.session))


def cases(harness: Harness) -> list[dict[str, Any]]:
    return [c for c in harness.cases.items.values() if c["kind"] == "case"]


def filed(harness: Harness) -> dict[str, Any]:
    (case,) = cases(harness)
    return case


def unsettled(chat: Chat) -> list[dict[str, Any]]:
    chat.say("Quiero bloquear una tarjeta.")
    chat.say("No sé.", requests=[])
    return chat.say("No recuerdo.", requests=[])


def pointed_twice(chat: Chat, **extracted: Any) -> list[dict[str, Any]]:
    control_shown(chat, **extracted)
    chat.say("sí", requests=[])
    return chat.say("sí, bloquéela", requests=[])


def test_two_unsettled_questions_offer_a_handoff_only_the_control_accepts(
    harness: Harness,
) -> None:
    chat = Chat(harness)

    stopped = unsettled(chat)

    offer = offer_of(stopped)
    assert shown(stopped) == [
        {
            "kind": "handoff_offer",
            "offer_id": offer["offer_id"],
            "reason_code": "clarification_failed",
        }
    ]
    assert chat.decision()["awaiting"] == "handoff_control"
    (recorded,) = [e for e in chat.entries() if e["kind"] == "interrupt"]
    assert recorded["controls"] == shown(stopped)
    assert cases(harness) == []

    accepted = answer_offer(chat, "accept", stopped)

    case = filed(harness)
    payload = case["payload"]
    assert (payload["trigger"], payload["reason_code"], payload["rules"]) == (
        "accepted_offer",
        "clarification_failed",
        ["POL-17"],
    )
    cards = {f["id"] for f in payload["verified_facts"] if f["subject"] == "card"}
    assert cards == {"PRD-EXAMPLE00002", "PRD-EXAMPLE00005"}
    assert reply(accepted) == FIXED["handoff_filed"]["es"].format(
        reference=case["reference"]
    )
    assert accepted[-1]["outcome"] == {"type": "success"}
    assert chat.decision() == {
        "request_label": "block_card",
        "outcome_class": "hand_off",
        "awaiting": "none",
        "rules": ["POL-13", "POL-14", "POL-17", "POL-45"],
    }
    resume = next(e for e in chat.entries() if e["kind"] == "resume")
    assert (resume["resume_kind"], resume["offer_id"], resume["accepted"]) == (
        "accept",
        offer["offer_id"],
        True,
    )
    assert case["flagged"] is False


def test_a_declined_offer_files_nothing(harness: Harness) -> None:
    chat = Chat(harness)
    stopped = unsettled(chat)

    declined = answer_offer(chat, "decline", stopped)

    assert reply(declined) == FIXED["offer_declined"]["es"]
    assert declined[-1]["outcome"] == {"type": "success"}
    assert cases(harness) == []
    assert chat.decision()["outcome_class"] == "answer"
    again = answer_offer(chat, "accept", stopped)
    assert [e["type"] for e in again] == ["RUN_ERROR"]


def test_a_verified_lost_block_offers_a_person_for_the_replacement(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    done = chat.press("confirm", control_shown(chat))

    assert offer_of(done)["reason_code"] == "unsupported_request"

    answer_offer(chat, "accept", done)

    case = filed(harness)
    payload = case["payload"]
    assert (payload["reason_code"], payload["rules"], payload["trigger"]) == (
        "unsupported_request",
        ["POL-38"],
        "accepted_offer",
    )
    (action,) = payload["actions"]
    assert (action["outcome"], action["reason"]) == ("verified", "lost")
    assert (case["priority"], case["flagged"]) == ("normal", False)


def test_a_failed_read_offers_a_handoff_citing_the_failed_call(
    harness: Harness,
) -> None:
    harness.script.gateway = lambda _: tool_error()
    chat = Chat(harness)

    failed = chat.say(
        "¿Cuáles son mis tarjetas?", requests=["card_status"], cards="all"
    )

    assert reply(failed) == "\n\n".join(
        [FIXED["unavailable"]["es"], FIXED["handoff_offer"]["es"]]
    )
    assert offer_of(failed)["reason_code"] == "tool_failure"
    assert chat.decision()["rules"] == ["POL-48"]

    answer_offer(chat, "accept", failed)

    payload = filed(harness)["payload"]
    assert payload["request"]["label"] == "card_status"
    assert ("list_cards", "error") in {
        (e["tool"], e["outcome"]) for e in payload["evidence"]
    }


def test_a_failed_router_offers_nothing(harness: Harness) -> None:
    harness.script.route_error = RuntimeError("provider down")
    chat = Chat(harness)

    failed = chat.say("¿Cuáles son mis tarjetas?")

    assert reply(failed) == FIXED["unavailable"]["es"]
    assert failed[-1]["outcome"] == {"type": "success"}


def test_typed_text_never_accepts_an_offer_and_a_new_request_ends_it(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    stopped = unsettled(chat)

    pointed = chat.say("sí, acepto", requests=[])

    assert reply(pointed) == FIXED["offer_pointer"]["es"]
    assert offer_of(pointed) == offer_of(stopped)
    assert chat.decision()["awaiting"] == "handoff_control"
    assert cases(harness) == []

    moved_on = chat.say(
        "¿Cuáles son mis tarjetas?", requests=["card_status"], cards="all"
    )

    assert moved_on[-1]["outcome"] == {"type": "success"}
    assert reply(moved_on) == cards_answer(harness.bank)
    assert cases(harness) == []


def test_asking_for_a_person_during_an_offer_hands_off_at_once(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    unsettled(chat)

    chat.say("Quiero hablar con una persona.", requests=["talk_to_human"])

    payload = filed(harness)["payload"]
    assert (payload["reason_code"], payload["trigger"]) == (
        "customer_request",
        "required",
    )


def test_a_second_pointer_offers_a_handoff_beside_the_confirmation(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    control_shown(chat)

    once = chat.say("sí", requests=[])
    assert [c["kind"] for c in shown(once)] == ["block_confirmation"]

    twice = chat.say("sí, bloquéela", requests=[])

    assert [c["kind"] for c in shown(twice)] == ["block_confirmation", "handoff_offer"]
    assert offer_of(twice)["reason_code"] == "clarification_failed"
    assert reply(twice) == "\n\n".join(
        [FIXED["control_pointer"]["es"], FIXED["handoff_offer"]["es"]]
    )
    assert chat.decision()["awaiting"] == "confirm_control"


def test_accepting_the_offer_ends_the_confirmation_beside_it_unused(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    twice = pointed_twice(chat)
    confirmation_id = shown(twice)[0]["confirmation_id"]

    accepted = answer_offer(chat, "accept", twice)

    case = filed(harness)
    assert reply(accepted).split("\n\n") == [
        FIXED["confirmation_lapsed"]["es"].format(
            card="tarjeta de crédito terminada en 4821"
        ),
        FIXED["handoff_filed"]["es"].format(reference=case["reference"]),
    ]
    assert harness.confirmations.records[confirmation_id]["status"] == "lapsed"
    lapse = [e for e in chat.entries() if e["kind"] == "confirmation"]
    assert [(e["to"], e["cause"]) for e in lapse] == [("lapsed", "handoff_accepted")]
    (action,) = case["payload"]["actions"]
    assert (action["outcome"], action["confirmed_at"]) == ("lapsed", None)
    # The card reported lost isn't blocked (POL-47).
    assert (case["priority"], case["flagged"]) == ("urgent", False)
    assert "block___block_card" not in [
        c["params"]["name"] for c in harness.script.tool_calls
    ]


def test_a_pending_unrecognized_charge_block_offers_no_handoff_beside_it(
    harness: Harness,
) -> None:
    chat = Chat(harness)

    twice = pointed_twice(chat, block_reason="unrecognized_charge")

    assert [c["kind"] for c in shown(twice)] == ["block_confirmation"]


def test_declining_the_offer_beside_a_confirmation_leaves_it_pending(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    twice = pointed_twice(chat)

    declined = answer_offer(chat, "decline", twice)

    assert reply(declined) == FIXED["offer_declined"]["es"]
    assert [c["kind"] for c in shown(declined)] == ["block_confirmation"]
    done = chat.press("confirm", declined)
    assert "quedó bloqueada" in reply(done)


def test_confirming_ends_the_offer_beside_the_confirmation(harness: Harness) -> None:
    chat = Chat(harness)
    twice = pointed_twice(chat, block_reason="other_reason")

    done = chat.press("confirm", twice)

    assert "quedó bloqueada" in reply(done)
    assert done[-1]["outcome"] == {"type": "success"}
    stale = answer_offer(chat, "accept", twice)
    assert [e["type"] for e in stale] == ["RUN_ERROR"]
    assert cases(harness) == []


def test_an_offer_from_an_earlier_sign_in_has_lapsed(harness: Harness) -> None:
    chat = Chat(harness)
    stopped = unsettled(chat)
    again = Customer(chat.who.sub, str(uuid.uuid4()), chat.who.customer_id)

    lapsed = answer_offer(chat, "accept", stopped, who=again)

    assert reply(lapsed) == FIXED["offer_lapsed"]["es"]
    assert cases(harness) == []
    resume = next(
        e for e in harness.records.of(again.origin_jti) if e["kind"] == "resume"
    )
    assert (resume["accepted"], resume["refusal"]) == (False, "other_sign_in")
    assert json.dumps(lapsed).count("handoff_offer") == 0
