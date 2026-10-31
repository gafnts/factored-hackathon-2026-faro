"""
A charge the customer doesn't recognize, end to end through the entrypoint and the real wrapper: the card, the charge
looked for in its 90-day window, the block offered with the confirm control when the card is Active, and one handoff to
dispute intake however that ends, with what couldn't be found or read recorded in it. The model reads the window
numbered and never an ID, and neither is_fraud nor a transaction's ID reaches the chat (ADR-0004, The graph; POL-13 to
POL-17, POL-27, POL-36, POL-39, POL-40, POL-47; CTL-03, CTL-05, AI-02).
"""

import json
from typing import Any

import pytest

from faro.agent.payload import DONE
from faro.agent.texts import FIXED, render

from .conftest import Harness, tool_error
from .test_block import Chat, interrupt, reply

CARD = {
    "product_type": "Tarjeta Crédito",
    "last_four": "4821",
    "product_status": "Active",
}
REPORTED = "Não reconheço uma compra no meu cartão de crédito final 4821."


def items(harness: Harness, status: str) -> list[dict[str, Any]]:
    return [
        c
        for c in harness.cases.items.values()
        if c["kind"] == "case" and c["status"] == status
    ]


def filed(harness: Harness) -> dict[str, Any]:
    (case,) = items(harness, "filed")
    return case


def window(harness: Harness) -> list[dict[str, Any]]:
    transactions: list[dict[str, Any]] = harness.bank.window["transactions"]
    return transactions


def reported(
    chat: Chat, text: str = REPORTED, **extracted: Any
) -> list[dict[str, Any]]:
    return chat.say(
        text,
        requests=["unrecognized_charge"],
        language="pt",
        **({"card_type": "credit", "last_four": "4821"} | extracted),
    )


def test_a_charge_is_found_and_the_block_offered_with_the_confirm_control(
    harness: Harness,
) -> None:
    chat = Chat(harness)

    shown = reported(chat)

    charge = window(harness)[0]
    # The example card is Active and past its recorded expiration: both facts are stated (POL-31).
    assert reply(shown) == "\n\n".join(
        [
            render("charge_found", "pt", {"card": CARD, "transaction": charge}),
            render("past_expiration", "pt", {"card": CARD}),
            FIXED["charge_reviewed"]["pt"],
            render(
                "confirm_prompt", "pt", {"card": CARD, "reason": "unrecognized_charge"}
            ),
        ]
    )
    control = interrupt(shown)["metadata"]["controls"][0]
    assert (control["reason"], control["card"]["last_four"]) == (
        "unrecognized_charge",
        "4821",
    )
    decision = chat.decision()
    assert (
        decision["request_label"],
        decision["outcome_class"],
        decision["awaiting"],
    ) == ("unrecognized_charge", "block", "confirm_control")
    assert {"POL-27", "POL-36", "POL-39"} <= set(decision["rules"])
    (draft,) = items(harness, "draft")
    assert draft["draft"]["reason_code"] == "unrecognized_charge"
    (choice,) = [e for e in chat.entries() if e.get("node") == "find_transaction"]
    assert (choice["purpose"], choice["output"]) == (
        "extract",
        {"extracted": {"fitting": "1", "language": "pt", "question": None}},
    )


def test_a_charge_reported_after_a_cards_read_is_about_that_card(
    harness: Harness,
) -> None:
    # POL-13, version 3: the message names no card, so the one the read settled on is meant.
    chat = Chat(harness)
    harness.script.replies = ["{card}, {window.from} - {window.to}:\n\n{transactions}"]
    chat.say(
        "Minhas transações do cartão de crédito final 4821.",
        requests=["recent_transactions"],
        language="pt",
        card_type="credit",
        last_four="4821",
    )

    shown = reported(chat, "Não reconheço essa compra.", card_type=None, last_four=None)

    charge = window(harness)[0]
    assert reply(shown).startswith(
        render("charge_found", "pt", {"card": CARD, "transaction": charge})
    )
    control = interrupt(shown)["metadata"]["controls"][0]
    assert control["card"]["last_four"] == "4821"


def test_the_charge_is_matched_against_the_message_that_reported_it(
    harness: Harness,
) -> None:
    # After the which-card question, the model reads the report, not the answer "A 4821."
    chat = Chat(harness)
    reported(chat, "Não reconheço uma cobrança.", card_type=None, last_four=None)

    shown = chat.say("A 4821.", requests=[], card_type="credit", last_four="4821")

    (messages,) = harness.script.model_inputs["choose"]
    assert messages[-1].content == "Não reconheço uma cobrança."
    charge = window(harness)[0]
    assert reply(shown).startswith(
        render("charge_found", "pt", {"card": CARD, "transaction": charge})
    )


def test_the_model_reads_the_window_numbered_and_never_an_id(harness: Harness) -> None:
    chat = Chat(harness)

    reported(chat)

    (messages,) = harness.script.model_inputs["choose"]
    read = json.dumps([m.content for m in messages], ensure_ascii=False)
    assert "TRX-" not in read and "PRD-" not in read and "is_fraud" not in read
    listed = "1. Sunday 2026-06-14 21:07:33, Purchase, 189.9 USD, Comercio Ejemplo"
    assert listed in read


def test_the_model_counts_relative_dates_from_the_business_date(
    harness: Harness,
) -> None:
    # POL-19: the business clock, never the wall clock; code gives the weekdays.
    harness.script.fitting = [1, 2]
    chat = Chat(harness)
    reported(chat)
    harness.script.fitting = [2]

    chat.say("A de domingo.", requests=[])

    asked, answered = harness.script.model_inputs["choose"]
    for messages in (asked, answered):
        read = json.dumps([m.content for m in messages], ensure_ascii=False)
        assert "Today is Wednesday 2026-06-17, the bank's business date." in read


@pytest.mark.parametrize(
    ("answer", "outcome", "priority"),
    [("confirm", "verified", "normal"), ("cancel", "declined_by_customer", "urgent")],
)
def test_however_the_block_offer_ends_the_charge_goes_to_dispute_intake(
    harness: Harness, answer: str, outcome: str, priority: str
) -> None:
    chat = Chat(harness)
    shown = reported(chat)
    (draft,) = items(harness, "draft")

    ended = chat.press(answer, shown)

    case = filed(harness)
    payload = case["payload"]
    assert case["handoff_id"] == draft["handoff_id"]
    assert (case["queue"], case["priority"], payload["reason_code"]) == (
        "dispute_intake",
        priority,
        "unrecognized_charge",
    )
    assert payload["request"]["label"] == "unrecognized_charge"
    assert chat.decision()["outcome_class"] == "hand_off"
    (action,) = payload["actions"]
    assert action["outcome"] == outcome
    charge = window(harness)[0]["transaction_id"]
    finds = [
        e["call_id"]
        for e in harness.records.of(chat.who.origin_jti)
        if e["kind"] == "tool_call" and e["tool"] == "find_transactions"
    ]
    stated = {f["field"]: f for f in payload["verified_facts"] if f["id"] == charge}
    assert stated["amount"]["value"] == 189.9
    assert stated["amount"]["evidence"] in finds
    assert stated["product_id"]["value"] == "PRD-EXAMPLE00002"
    # file_handoff adds the bank's own flag, which the graph never read (POL-40).
    assert stated["is_fraud"]["evidence"] not in finds
    assert case["flagged"] is False
    sent = json.dumps(ended, ensure_ascii=False)
    assert "TRX-" not in sent and "is_fraud" not in sent
    assert reply(ended).endswith(
        FIXED["handoff_filed"]["pt"].format(reference=case["reference"])
    )
    # The model writes the notes before the turn's reply exists, so a block after the context says how the block ended.
    (notes,) = harness.script.model_inputs["handoff_text"]
    assert DONE[outcome] in json.dumps(notes[0].content, ensure_ascii=False)


def test_several_charges_that_fit_are_listed_for_the_customer_to_choose(
    harness: Harness,
) -> None:
    harness.script.fitting = [1, 2, 3]
    chat = Chat(harness)

    asked = reported(chat)

    listed = window(harness)[:3]
    assert reply(asked) == render(
        "which_charge", "pt", {"card": CARD, "transactions": listed}
    )
    assert chat.decision()["awaiting"] == "transaction"
    assert chat.decision()["outcome_class"] == "clarify"

    harness.script.fitting = [2]
    shown = chat.say("A segunda.", requests=[])

    assert reply(shown).startswith(
        render("charge_found", "pt", {"card": CARD, "transaction": listed[1]})
    )
    assert interrupt(shown)["metadata"]["controls"][0]["kind"] == "block_confirmation"
    (answering,) = harness.script.model_inputs["choose"][1:]
    assert "which of these transactions" in json.dumps([m.content for m in answering])


# A message that doesn't answer the question (POL-06, version 6)


def test_a_message_that_doesnt_answer_which_charge_gets_its_reply_and_the_question_again(
    harness: Harness,
) -> None:
    harness.script.fitting = [1, 2, 3]
    chat = Chat(harness)
    asked = reported(chat)

    aside = chat.say(
        "Em que mais você pode me ajudar?", requests=[], question="unanswered"
    )

    # One question: the capabilities without their own closing one, then the question asked.
    assert reply(aside) == "\n\n".join(
        [FIXED["capabilities_aside"]["pt"], reply(asked)]
    )
    assert reply(aside).count("?") == reply(asked).count("?")
    decision = chat.decision()
    assert (decision["outcome_class"], decision["awaiting"]) == (
        "clarify",
        "transaction",
    )
    assert "POL-06" in decision["rules"]
    assert not items(harness, "filed")
    # It counts toward no limit: the answer after it is still the first.
    harness.script.fitting = [2]
    shown = chat.say("A segunda.", requests=[])
    assert interrupt(shown)["metadata"]["controls"][0]["kind"] == "block_confirmation"


def test_a_new_request_while_which_charge_is_asked_files_the_dispute_then_is_served(
    harness: Harness,
) -> None:
    harness.script.fitting = [1, 2, 3]
    chat = Chat(harness)
    reported(chat)
    harness.script.replies = ["{card}: {card.status}, {card.expiration}."]

    served = chat.say(
        "Como está o meu débito final 1177?",
        requests=["card_status"],
        language="pt",
        question="unanswered",
        last_four="1177",
    )

    case = filed(harness)
    assert case["payload"]["reason_code"] == "unrecognized_charge"
    assert [
        (e["request_label"], e["outcome_class"], e["awaiting"])
        for e in chat.entries()
        if e["kind"] == "decision"
    ] == [
        ("unrecognized_charge", "hand_off", "none"),
        ("card_status", "answer", "none"),
    ]
    first, answered = reply(served).split("\n\n")
    assert first == FIXED["handoff_filed"]["pt"].format(reference=case["reference"])
    assert answered.startswith("Cartão de débito final 1177: ativo")


def test_a_charge_not_settled_after_two_questions_is_recorded_unfound(
    harness: Harness,
) -> None:
    harness.script.fitting = [1, 2]
    chat = Chat(harness)
    reported(chat)
    chat.say("Não sei.", requests=[])

    shown = chat.say("Não lembro.", requests=[])

    assert reply(shown).split("\n\n")[0] == FIXED["clarification_stopped"]["pt"]
    chat.press("cancel", shown)
    payload = filed(harness)["payload"]
    assert not [f for f in payload["verified_facts"] if f["subject"] == "transaction"]


def test_a_charge_that_fits_nothing_is_said_so_and_the_block_still_offered(
    harness: Harness,
) -> None:
    harness.script.fitting = []
    chat = Chat(harness)

    shown = reported(chat)

    assert reply(shown).split("\n\n")[0] == render(
        "charge_not_found", "pt", {"card": CARD}
    )
    assert interrupt(shown)


def test_a_window_that_cant_be_read_is_recorded_in_the_handoff(
    harness: Harness,
) -> None:
    bank = harness.bank.answer

    def fails_the_window(request: Any) -> Any:
        name = json.loads(request.content)["params"]["name"]
        return tool_error() if name == "reads___find_transactions" else bank(request)

    harness.script.gateway = fails_the_window
    chat = Chat(harness)

    shown = reported(chat)

    assert reply(shown).split("\n\n")[0] == render(
        "charge_unread", "pt", {"card": CARD}
    )
    chat.press("cancel", shown)
    evidence = filed(harness)["payload"]["evidence"]
    assert ("find_transactions", "error") in {
        (e["tool"], e["outcome"]) for e in evidence
    }


def test_a_charge_on_a_card_already_blocked_goes_to_dispute_intake_at_once(
    harness: Harness,
) -> None:
    harness.bank.blocked.add("PRD-EXAMPLE00002")
    chat = Chat(harness)

    answered = reported(chat)

    case = filed(harness)
    blocked = {**CARD, "product_status": "Blocked"}
    assert reply(answered) == "\n\n".join(
        [
            render(
                "charge_found",
                "pt",
                {"card": blocked, "transaction": window(harness)[0]},
            ),
            render("already_blocked", "pt", {"card": blocked}),
            FIXED["handoff_filed"]["pt"].format(reference=case["reference"]),
        ]
    )
    assert answered[-1]["outcome"] == {"type": "success"}
    assert (case["priority"], case["payload"]["actions"]) == ("normal", [])
    assert chat.decision()["outcome_class"] == "hand_off"


def test_a_charges_card_that_cant_be_settled_goes_to_dispute_intake_at_once(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    asked = reported(
        chat, "Não reconheço uma cobrança.", card_type=None, last_four=None
    )
    assert reply(asked) == render(
        "which_card_charge",
        "pt",
        {
            "cards": [
                c
                for c in harness.bank.listed["cards"]
                if c["product_status"] == "Active"
            ]
        },
    )
    chat.say("Não sei.", requests=[])

    stopped = chat.say("Não lembro.", requests=[])

    case = filed(harness)
    assert reply(stopped).split("\n\n") == [
        FIXED["clarification_stopped"]["pt"],
        FIXED["handoff_filed"]["pt"].format(reference=case["reference"]),
    ]
    assert stopped[-1]["outcome"] == {"type": "success"}
    assert (case["payload"]["reason_code"], case["priority"]) == (
        "unrecognized_charge",
        "urgent",
    )


def test_a_listing_that_fails_for_a_charge_is_recorded_in_its_handoff(
    harness: Harness,
) -> None:
    harness.script.gateway = lambda _: tool_error()
    chat = Chat(harness)

    answered = reported(chat)

    case = filed(harness)
    assert reply(answered).split("\n\n") == [
        FIXED["records_unavailable"]["pt"],
        FIXED["handoff_filed"]["pt"].format(reference=case["reference"]),
    ]
    assert (case["queue"], case["priority"]) == ("dispute_intake", "urgent")
    assert answered[-1]["outcome"] == {"type": "success"}
