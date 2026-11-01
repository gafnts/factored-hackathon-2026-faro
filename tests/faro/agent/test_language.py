"""
POL-50 and POL-51 as code applies them to the model's reading of each message (ADR-0004's amendment of 2026-10-02): es
or pt sets the conversation's language and unclear keeps it, Spanish until a message sets one; other gets POL-51's
reply whatever labels came with it, leaves a pending question pending, and points to a control that shows; a call that
fails or isn't made keeps the language (SCP-06, EVL-07).
"""

import pytest

from faro.agent.language import DEFAULT, Spoken, settled
from faro.agent.models import Language
from faro.agent.texts import FIXED

from .conftest import Harness, run_body
from .test_block import THREAD, Chat, interrupt, reply
from .test_charge import reported
from .test_reads import decisions, tools


@pytest.mark.parametrize(
    ("current", "found", "language"),
    [
        ("es", "pt", "pt"),
        ("pt", "es", "es"),
        ("pt", "unclear", "pt"),
        ("pt", "other", "pt"),
        ("pt", None, "pt"),
        (DEFAULT, "unclear", "es"),
    ],
)
def test_a_clear_message_sets_the_language_and_anything_else_keeps_it(
    current: Spoken, found: Language | None, language: Spoken
) -> None:
    assert settled(current, found) == language


def test_a_labeled_message_in_another_language_gets_only_the_fixed_reply(
    harness: Harness,
) -> None:
    chat = Chat(harness)

    events = chat.say(
        "Why was my card declined?", requests=["decline_reason"], language="other"
    )

    assert reply(events) == FIXED["third_language"]["es"]
    assert len(harness.script.model_inputs["route"]) == 1
    assert tools(harness) == []
    (decided,) = decisions(chat)
    assert decided == {
        "request_label": None,
        "outcome_class": "decline",
        "awaiting": "none",
        "rules": ["POL-51"],
        "pending_labels": [],
    }


def test_an_answer_in_another_language_leaves_the_card_question_pending(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    chat.say("¿Cómo está mi tarjeta?", requests=["card_status"], language="es")
    assert chat.decision()["awaiting"] == "card"

    events = chat.say("Which one? I have several.", requests=[], language="other")

    assert reply(events) == FIXED["third_language"]["es"]
    assert chat.decision() == {
        "request_label": None,
        "outcome_class": "decline",
        "awaiting": "card",
        "rules": ["POL-51"],
    }
    # The cards are read before the extraction, as for any answer (POL-12).
    assert tools(harness) == ["list_cards", "list_cards"]

    harness.script.replies = ["{card}: {card.status}, {card.expiration}."]
    chat.say("La 1177.", requests=[], last_four="1177")

    assert chat.decision()["outcome_class"] == "answer"


def test_an_answer_in_another_language_leaves_the_charge_question_pending(
    harness: Harness,
) -> None:
    harness.script.fitting = [1, 2, 3]
    chat = Chat(harness)
    reported(chat)
    assert chat.decision()["awaiting"] == "transaction"

    events = chat.say("The second one, I think.", requests=[], language="other")

    assert reply(events) == FIXED["third_language"]["pt"]
    assert chat.decision() == {
        "request_label": None,
        "outcome_class": "decline",
        "awaiting": "transaction",
        "rules": ["POL-51"],
    }
    assert len(harness.script.model_inputs["choose"]) == 2


def test_another_language_while_the_control_shows_leaves_it_pending(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = chat.say(
        "Bloquear la 1177 por pérdida",
        language="es",
        last_four="1177",
        block_reason="lost",
    )

    events = chat.say("Please block my card now", requests=[], language="other")

    assert reply(events).startswith(FIXED["third_language"]["es"])
    assert (
        interrupt(events)["metadata"]["controls"]
        == interrupt(shown)["metadata"]["controls"]
    )
    (decided,) = decisions(chat)
    assert decided["awaiting"] == "confirm_control"
    assert "POL-51" in decided["rules"]


@pytest.mark.parametrize(
    ("first", "language"), [("pt", "pt"), ("unclear", "es"), ("es", "es")]
)
def test_a_message_that_isnt_clear_keeps_the_language_the_last_clear_one_set(
    harness: Harness, first: str, language: str
) -> None:
    chat = Chat(harness)
    chat.say("Perdi meu cartão de crédito.", language=first, card_type="credit")
    assert chat.decision()["awaiting"] == "reason"

    shown = chat.say("Ok.", requests=[], language="unclear", block_reason="lost")

    assert interrupt(shown)["metadata"]["language"] == language
    assert harness.script.model_inputs["choose"] == []


def test_a_failed_router_keeps_the_conversations_language(harness: Harness) -> None:
    chat = Chat(harness)
    chat.say(
        "Quais são os meus cartões?",
        requests=["card_status"],
        language="pt",
        cards="all",
    )
    harness.script.route_error = RuntimeError("provider down")

    events = chat.say("E o limite?", requests=["available_credit"], language="es")

    assert reply(events).startswith(FIXED["unavailable"]["pt"])


def test_a_charge_whose_extraction_fails_keeps_the_language(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    harness.script.extract_error = RuntimeError("provider down")

    shown = reported(chat)

    # The card is read from the digits the message names, and the reply stays in Portuguese.
    assert interrupt(shown)["metadata"]["language"] == "pt"
    assert "cartão" in reply(shown)
    assert "tarjeta" not in reply(shown)


def test_a_later_call_reading_the_same_message_keeps_the_first_reading(
    harness: Harness,
) -> None:
    # D-008: the router read a Portuguese message as Portuguese and the card extraction read it as Spanish.
    chat = Chat(harness)
    script = harness.script
    script.requests, script.has_request = ["card_status"], True
    script.language = "pt"
    script.extracted = {"language": "es", "last_four": "1177"}

    events = chat.check(
        harness.post(
            run_body("Como está meu débito 1177?", thread=THREAD),
            chat.who.token(),
            chat.session,
        )
    )

    decision = [e for e in chat.entries() if e["kind"] == "decision"][0]
    assert decision["language"] == "pt"
    assert tools(harness) == ["list_cards", "get_card"]
    assert "cartão" in reply(events)
    assert "tarjeta" not in reply(events)
