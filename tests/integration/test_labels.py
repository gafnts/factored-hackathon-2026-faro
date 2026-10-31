"""
One request per label the reads and the declines added, played against the deployed stack by the personas, each graded
by its decision entry and by the facts its reply states, formatted as the tools' own results say they should be
(ADR-0004's amendment of 2026-10-01): the credit available (POL-01, POL-22 to POL-24), recent transactions (POL-19,
POL-25), a decline explained (POL-02, POL-27 to POL-29), a card service the chat doesn't serve (POL-42), an unblock
(POL-41), a person (POL-44), two requests in one message (POL-05), and a third language (POL-51); with the reply check's
entry for each answer the model wrote (decision 8; AI-03, CTL-01, CTL-03, SCP-04, SCP-06). Assertions compare without
printing a card, a customer's text, an amount, or an ID.
"""

from collections.abc import Iterator
from typing import Any

import pytest

from faro.agent.formats import MEANINGS, amount, moment, transaction_name
from faro.agent.texts import FIXED

from .conftest import SignIn, User, handoffs, stored
from .test_block import Conversation, interrupt, reply
from .test_handoff import window
from .test_stack import arguments, call, tool_output

pytestmark = pytest.mark.integration


@pytest.fixture
def persona(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn, saved: list[str]
) -> Iterator[Any]:
    started: list[Conversation] = []

    def start(role: str) -> tuple[Conversation, dict[str, Any]]:
        language = "es" if role == "customer" else "pt"
        access = sign_in(users[role], "customer")["access"]
        started.append(Conversation(outputs, access, language))
        listed = tool_output(call(outputs, access, "list_cards", arguments(access)))
        return started[-1], listed

    yield start
    for chat in started:
        saved.extend(handoffs(chat.entries()))


def decisions(chat: Conversation) -> list[dict[str, Any]]:
    return [e for e in chat.last_turn() if e["kind"] == "decision"]


def checked(chat: Conversation) -> list[dict[str, Any]]:
    return [e for e in chat.last_turn() if e["kind"] == "reply_check"]


def credit_cards(listed: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        c
        for c in listed["cards"]
        if c["product_type"] == "Tarjeta Crédito" and c["product_status"] == "Active"
    ]


def test_a_persona_hears_the_credit_the_tool_computed(
    outputs: dict[str, Any], persona: Any
) -> None:
    chat, listed = persona("other_customer")
    card = credit_cards(listed)[0]
    read = tool_output(
        call(
            outputs,
            chat.access,
            "get_available_credit",
            arguments(chat.access, card_id=card["card_id"]),
        )
    )["card"]
    country = listed["customer"]["country"]

    events = chat.say(
        f"Quanto crédito disponível tem o meu cartão de crédito final {card['last_four']}?"
    )

    (decided,) = decisions(chat)
    assert (decided["request_label"], decided["outcome_class"]) == (
        "available_credit",
        "answer",
    )
    figure = (
        read["available_credit"]
        if read["availability"] == "available"
        else read["over_limit_by"]
    )
    stated = amount(figure, read["currency"], country) in reply(events)
    assert stated, "the reply doesn't state the tool's figure as formatted"
    assert len(checked(chat)) == 1
    tools = [e["tool"] for e in chat.last_turn() if e["kind"] == "tool_call"]
    assert tools == ["list_cards", "get_available_credit"]


def test_a_persona_reads_a_page_of_recent_transactions_with_its_window(
    outputs: dict[str, Any], persona: Any
) -> None:
    chat, listed = persona("other_customer")
    card = credit_cards(listed)[0]
    page = tool_output(
        call(
            outputs,
            chat.access,
            "find_transactions",
            arguments(chat.access, card_id=card["card_id"]),
        )
    )
    country = listed["customer"]["country"]

    events = chat.say(
        f"Quais são as transações recentes do meu cartão final {card['last_four']}?"
    )

    (decided,) = decisions(chat)
    assert (decided["request_label"], decided["outcome_class"]) == (
        "recent_transactions",
        "answer",
    )
    text = reply(events)
    ends = [moment(page["window"]["from"]), moment(page["window"]["to"])]
    assert all(end in text for end in ends), "the reply doesn't state the window"
    newest = page["transactions"][0]
    shown = amount(newest["amount"], newest["currency"], country) in text
    assert shown, "the reply doesn't list the newest transaction's amount"
    more = FIXED["transactions_more"]["pt"] in text
    assert more == (page["next_cursor"] is not None)


def test_a_persona_hears_why_a_payment_was_declined(
    outputs: dict[str, Any], persona: Any
) -> None:
    # The es persona has a decline with a listed code in the last week (personas.py).
    chat, listed = persona("customer")
    country = listed["customer"]["country"]
    found = [
        (card, t)
        for card in listed["cards"]
        for t in window(outputs, chat.access, card["card_id"])
        if t["transaction_status"] == "Declined" and t["response_meaning"]
    ]
    card, declined = max(found, key=lambda pair: pair[1]["transaction_date"])
    date = declined["transaction_date"]
    spoken = amount(declined["amount"], declined["currency"], country)

    events = chat.say(
        f"¿Por qué me rechazaron el pago de {spoken} del {date[8:10]}/{date[5:7]} "
        f"con mi tarjeta terminada en {card['last_four']}?"
    )
    if decisions(chat)[-1]["outcome_class"] == "clarify":
        events = chat.say(f"Es este: {transaction_name(declined, 'es', country)}")

    (decided,) = decisions(chat)
    assert (decided["request_label"], decided["outcome_class"]) == (
        "decline_reason",
        "answer",
    )
    explained = MEANINGS["es"][declined["response_meaning"]] in reply(events)
    assert explained, "the reply doesn't give the code's meaning"


def test_a_card_service_the_chat_doesnt_serve_is_declined_with_a_person_offered(
    persona: Any,
) -> None:
    chat, _ = persona("customer")

    events = chat.say("Quiero cambiar el PIN de mi tarjeta.")

    (decided,) = decisions(chat)
    assert (
        decided["request_label"],
        decided["outcome_class"],
        decided["awaiting"],
        decided["rules"],
    ) == ("unsupported", "decline", "handoff_control", ["POL-42"])
    controls = interrupt(events)["metadata"]["controls"]
    assert [c["reason_code"] for c in controls] == ["unsupported_request"]


def test_an_unblock_is_handed_off(outputs: dict[str, Any], persona: Any) -> None:
    chat, _ = persona("customer")

    events = chat.say("Necesito que desbloqueen mi tarjeta, por favor.")

    (decided,) = decisions(chat)
    assert (decided["request_label"], decided["outcome_class"]) == (
        "unsupported",
        "hand_off",
    )
    assert reply(events).startswith(FIXED["unblock_by_person"]["es"])
    (filed,) = [e for e in chat.last_turn() if e["kind"] == "handoff"]
    case = stored(outputs, filed["handoff_id"])
    assert case is not None
    assert (case["payload"]["reason_code"], case["payload"]["trigger"]) == (
        "unblock_request",
        "required",
    )


def test_asking_for_a_person_is_handed_off(
    outputs: dict[str, Any], persona: Any
) -> None:
    chat, _ = persona("other_customer")

    chat.say("Quero falar com uma pessoa, por favor.")

    (decided,) = decisions(chat)
    assert (decided["request_label"], decided["outcome_class"]) == (
        "talk_to_human",
        "hand_off",
    )
    (filed,) = [e for e in chat.last_turn() if e["kind"] == "handoff"]
    assert filed["reason_code"] == "customer_request"


def test_two_requests_in_one_message_are_served_in_order(persona: Any) -> None:
    chat, listed = persona("other_customer")
    card = credit_cards(listed)[0]

    chat.say(
        f"Quanto crédito tem o meu cartão final {card['last_four']}, "
        "e qual é o status dos meus cartões?"
    )

    served = [(d["request_label"], d["pending_labels"]) for d in decisions(chat)]
    assert served == [
        ("card_status", ["available_credit"]),
        ("available_credit", []),
    ]


def test_a_third_language_gets_spanish_with_one_portuguese_sentence(
    persona: Any,
) -> None:
    chat, _ = persona("customer")

    events = chat.say("Why was my card declined last week?")

    assert reply(events) == FIXED["third_language"]["es"]
    (decided,) = decisions(chat)
    assert (decided["request_label"], decided["outcome_class"], decided["rules"]) == (
        None,
        "decline",
        ["POL-51"],
    )
