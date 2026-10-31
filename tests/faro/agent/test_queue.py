"""
POL-05's queue: the requests one message holds are served in its order, the next once the one before it waits on
nothing, in one reply with a decision entry each; a request that waits on a question or a control keeps the rest
queued, named in the reply, until it ends; and a new request replaces them (ADR-0004's amendment of 2026-10-01; AI-01).
"""

from typing import Any

from faro.agent.texts import FIXED, render

from .conftest import Harness, cards_answer, list_cards_output, tool_result
from .test_block import Chat, reply
from .test_offer import cases
from .test_reads import decisions, tools

TWO = "¿Por qué me rechazaron un pago, y cómo está mi tarjeta?"


def test_two_requests_are_served_in_order_in_one_reply(harness: Harness) -> None:
    chat = Chat(harness)

    events = chat.say(
        "¿Qué tarjetas tengo y cuáles son mis movimientos?",
        requests=["recent_transactions", "card_status"],
        cards="all",
    )

    answer, question = reply(events).split("\n\n¿")
    assert answer == cards_answer(harness.bank)
    assert f"¿{question}".startswith(FIXED["which_card_read"]["es"].split("\n")[0])
    assert [
        (d["request_label"], d["outcome_class"], d["pending_labels"])
        for d in decisions(chat)
    ] == [
        ("card_status", "answer", ["recent_transactions"]),
        ("recent_transactions", "clarify", []),
    ]
    assert decisions(chat)[-1]["awaiting"] == "card"


def test_the_rest_wait_through_a_question_and_are_served_after_it(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    harness.script.replies = [
        "{card}:\n{transaction}\n{transaction.meaning}",
        "{card}: {card.status}, {card.expiration}.",
    ]

    asked = chat.say(TWO, requests=["decline_reason", "card_status"])

    assert reply(asked).endswith(render("queued", "es", {"requests": ["card_status"]}))
    (waiting,) = decisions(chat)
    assert (waiting["awaiting"], waiting["pending_labels"]) == ("card", ["card_status"])

    harness.script.fitting = [2]
    answered = chat.say("La de crédito 4821", requests=[], last_four="4821")

    explained, status, conflict = reply(answered).split("\n\n")
    assert explained.endswith("\n- Motivo: fondos insuficientes")
    assert status == "Tarjeta de crédito terminada en 4821: activa, 02/2026."
    assert conflict == FIXED["past_expiration"]["es"].format(
        card="tarjeta de crédito terminada en 4821"
    )
    assert [(d["request_label"], d["pending_labels"]) for d in decisions(chat)] == [
        ("decline_reason", ["card_status"]),
        ("card_status", []),
    ]
    # The router read the first message only: the answer went back to the step that asked (POL-06).
    assert len(harness.script.model_inputs["route"]) == 1


def test_the_rest_wait_through_the_confirm_control_and_follow_the_block(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    harness.script.replies = ["{card}: {card.status}, {card.expiration}."]

    shown = chat.say(
        "Bloqueen la 1177 y díganme cómo queda",
        requests=["card_status", "block_card"],
        last_four="1177",
        block_reason="other_reason",
    )

    assert reply(shown).endswith(render("queued", "es", {"requests": ["card_status"]}))
    blocked = chat.press("confirm", shown)

    verified, status = reply(blocked).split("\n\n")
    assert verified == render("block_verified", "es", {"card": harness.bank.cards()[2]})
    assert status == "Tarjeta de débito terminada en 1177: bloqueada, no registrada."
    assert [(d["request_label"], d["outcome_class"]) for d in decisions(chat)] == [
        ("block_card", "block"),
        ("card_status", "answer"),
    ]


def test_a_new_request_replaces_what_was_queued(harness: Harness) -> None:
    chat = Chat(harness)
    chat.say(
        "Bloqueen la 1177 y díganme cómo queda",
        requests=["card_status", "block_card"],
        last_four="1177",
        block_reason="lost",
    )
    calls = len(harness.script.tool_calls)

    moved_on = chat.say(
        "Mejor quiero hablar con una persona", requests=["talk_to_human"]
    )

    assert reply(moved_on).startswith(
        FIXED["confirmation_lapsed"]["es"].split("{card}")[0]
    )
    assert tools(harness)[calls:] == ["list_cards"]
    # The lost card's draft is left to expire: a new request ends the confirmation without a handoff (POL-38).
    (filed,) = [c for c in cases(harness) if c["status"] == "filed"]
    assert filed["payload"]["reason_code"] == "customer_request"
    (decided,) = decisions(chat)
    assert (decided["request_label"], decided["pending_labels"]) == (
        "talk_to_human",
        [],
    )


def test_a_customer_not_served_in_full_is_handed_off_once_for_every_request(
    harness: Harness,
) -> None:
    # POL-12.
    def not_served(request: Any) -> Any:
        output = list_cards_output()
        output["customer"]["served_in_full"] = False
        return tool_result(output)

    harness.script.gateway = not_served
    chat = Chat(harness)

    chat.say(
        "¿Cómo están mis tarjetas y mis movimientos?",
        requests=["card_status", "recent_transactions"],
    )

    (filed,) = cases(harness)
    assert filed["payload"]["reason_code"] == "customer_not_active"
    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["pending_labels"]) == ("hand_off", [])
