"""
The scripted customer: what it sends for each thing the agent awaits, from the script alone, and when it stops.
"""

from typing import Any

import pytest

from faro.evaluation.customer import Customer, ScriptError

CONFIRMATION = "7f0f6a52-1d1e-4a39-9a65-0c2b6f1f3c11"
OFFER = "2b8f8d0c-5a43-4f61-8b8e-61f0a7a1d2e4"


def case(
    answers: dict[str, Any] | None = None, turns: int = 2, **script: Any
) -> dict[str, Any]:
    return {
        "script": {
            "messages": [
                {"id": "block_card-01/es/0", "text": "Quiero bloquear mi tarjeta."},
                {"id": "card_status-01/es/0", "text": "¿Cómo está mi tarjeta?"},
            ],
            "answers": answers or {},
            "means": {},
            **script,
        },
        "expected": {"turns": [{}] * turns},
    }


def interrupt(*kinds: str) -> dict[str, Any]:
    controls = {
        "block_confirmation": {
            "kind": "block_confirmation",
            "confirmation_id": CONFIRMATION,
        },
        "handoff_offer": {
            "kind": "handoff_offer",
            "offer_id": OFFER,
            "reason_code": "tool_failure",
        },
    }
    return {
        "id": "interrupt-1",
        "reason": "controls",
        "metadata": {"language": "es", "controls": [controls[k] for k in kinds]},
    }


def test_it_opens_with_the_first_message_and_sends_the_next_when_nothing_is_awaited() -> (
    None
):
    customer = Customer(case())

    assert customer.first().text == "Quiero bloquear mi tarjeta."
    sent = customer.next("none", None)

    assert sent is not None
    assert (sent.sends, sent.text_id) == ("message", "card_status-01/es/0")
    assert customer.next("none", None) is None


def test_it_answers_what_is_asked_and_says_it_doesnt_know_otherwise() -> None:
    customer = Customer(
        case(
            {
                "card": {"id": "card_type-01/es", "text": "La de crédito."},
                "dont_know": {"id": "dont_know-01/es", "text": "No lo sé."},
            }
        )
    )
    customer.first()

    card = customer.next("card", None)
    reason = customer.next("reason", None)

    assert card is not None and (card.sends, card.text) == ("card", "La de crédito.")
    assert reason is not None and (reason.sends, reason.text) == (
        "dont_know",
        "No lo sé.",
    )


def test_it_presses_the_control_its_script_names_with_the_shown_id() -> None:
    customer = Customer(
        case({"confirm_control": "confirm", "handoff_control": "accept"}, turns=3)
    )
    customer.first()

    confirm = customer.next("confirm_control", interrupt("block_confirmation"))
    accept = customer.next(
        "handoff_control", interrupt("block_confirmation", "handoff_offer")
    )

    assert confirm is not None and confirm.sends == "confirm"
    assert confirm.resume == {
        "interruptId": "interrupt-1",
        "status": "resolved",
        "payload": {"kind": "confirm", "confirmation_id": CONFIRMATION},
    }
    assert accept is not None and accept.resume is not None
    assert accept.resume["payload"] == {"kind": "accept", "offer_id": OFFER}


def test_a_typed_yes_comes_first_and_the_control_after_it() -> None:
    customer = Customer(
        case(
            {
                "typed_yes": {"id": "typed_yes-01/es", "text": "Sí, bloquéela."},
                "confirm_control": "cancel",
            },
            turns=3,
        )
    )
    customer.first()

    typed = customer.next("confirm_control", interrupt("block_confirmation"))
    pressed = customer.next("confirm_control", interrupt("block_confirmation"))

    assert typed is not None and (typed.sends, typed.resume) == ("typed_yes", None)
    assert pressed is not None and pressed.sends == "cancel"


def test_a_message_that_doesnt_answer_comes_once_before_the_answer() -> None:
    customer = Customer(
        case(
            {
                "aside": {
                    "id": "aside-01/es",
                    "text": "¿Y en qué más me puede ayudar?",
                },
                "card": {
                    "id": "card_last_four-01/es",
                    "text": "La que termina en 4821.",
                },
            },
            turns=3,
        )
    )
    customer.first()

    aside = customer.next("card", None)
    answer = customer.next("card", None)

    assert aside is not None and aside.sends == "aside"
    assert answer is not None and answer.sends == "card"


def test_an_ignored_control_lapses_by_the_next_message_or_ends_the_conversation() -> (
    None
):
    customer = Customer(case({"handoff_control": "ignore"}))
    customer.first()

    lapsed = customer.next("handoff_control", interrupt("handoff_offer"))

    assert lapsed is not None and lapsed.sends == "message"
    assert customer.next("confirm_control", interrupt("block_confirmation")) is None


def test_it_stops_at_twice_the_expected_turns_and_when_no_decision_was_recorded() -> (
    None
):
    answers = {"dont_know": {"id": "dont_know-01/es", "text": "No lo sé."}}
    customer = Customer(case(answers, turns=1))
    customer.first()

    assert customer.next("card", None) is not None
    assert customer.next("card", None) is None
    assert Customer(case(answers)).next(None, None) is None


@pytest.mark.parametrize(
    ("script", "awaiting", "shown"),
    [
        ({"actions": [{"before_turn": 2, "action": "sign_out"}]}, None, None),
        ({}, "reason", None),
        ({"answers": {"confirm_control": "confirm"}}, "confirm_control", None),
        (
            {"answers": {"confirm_control": "confirm"}},
            "confirm_control",
            interrupt("handoff_offer"),
        ),
    ],
    ids=["harness actions", "no answer", "no interrupt", "another control"],
)
def test_a_script_it_cant_play_is_the_players_error(
    script: dict[str, Any], awaiting: str | None, shown: dict[str, Any] | None
) -> None:
    answers = script.pop("answers", {})
    with pytest.raises(ScriptError):
        customer = Customer(case(answers, **script))
        customer.first()
        customer.next(awaiting, shown)
