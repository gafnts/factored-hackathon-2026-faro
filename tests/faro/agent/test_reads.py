"""
The reads, the declines, and the requests the chat doesn't serve, through the entrypoint with scripted models, each with
the outcome ADR-0004's amendment of 2026-10-01 lists for it: a card's status (POL-01, POL-13 to POL-16, POL-21, POL-31),
the credit available (POL-22 to POL-24), recent transactions and the next page (POL-19, POL-25), a decline explained
(POL-02, POL-27 to POL-30, POL-32), the requests the chat declines or hands off (POL-08, POL-41 to POL-43), and a third
language (POL-51); with the reply check over every answer the model writes (decision 8; AI-03, CTL-01, CTL-03, SCP-04,
SCP-06).
"""

import copy
import json
from typing import Any

import pytest

from faro.agent.graph import SHAPES
from faro.agent.nodes.resolve import EARLIER
from faro.agent.texts import FIXED, render

from .conftest import Harness, detail, tool_result
from .test_block import Chat, interrupt, reply
from .test_offer import cases

CARD = "tarjeta de crédito terminada en 4821"
CREDIT = "PRD-EXAMPLE00002"
CLOSED = "PRD-EXAMPLE00007"
DEBIT = "PRD-EXAMPLE00005"


def extracted(harness: Harness, n: int) -> str:
    content: Any = harness.script.model_inputs["extract"][n][0].content
    text: str = content[1]["text"]
    return text


def decisions(chat: Chat) -> list[dict[str, Any]]:
    return [
        {
            k: e[k]
            for k in (
                "request_label",
                "outcome_class",
                "awaiting",
                "rules",
                "pending_labels",
            )
        }
        for e in chat.entries()
        if e["kind"] == "decision"
    ]


def tools(harness: Harness) -> list[str]:
    return [c["params"]["name"].rpartition("___")[2] for c in harness.script.tool_calls]


def offered(events: list[dict[str, Any]]) -> str:
    controls = interrupt(events)["metadata"]["controls"]
    (control,) = [c for c in controls if c["kind"] == "handoff_offer"]
    reason: str = control["reason_code"]
    return reason


def checks(chat: Chat) -> list[dict[str, Any]]:
    return [e for e in chat.entries() if e["kind"] == "reply_check"]


def credit(harness: Harness, availability: str, **figures: Any) -> None:
    harness.bank.credit[CREDIT]["card"] = {
        **{
            k: v
            for k, v in harness.bank.credit[CREDIT]["card"].items()
            if k
            not in (
                "credit_limit",
                "current_balance",
                "available_credit",
                "over_limit_by",
            )
        },
        "availability": availability,
        **figures,
    }


# A card's status


@pytest.mark.parametrize("language", ["es", "pt"])
def test_one_card_named_gets_its_status_and_expiration(
    harness: Harness, language: str
) -> None:
    chat = Chat(harness)
    harness.script.replies = ["{card}: {card.status}, {card.expiration}."]
    text = {"es": "¿Cómo está mi débito 1177?", "pt": "Como está meu débito 1177?"}

    events = chat.say(
        text[language], requests=["card_status"], language=language, last_four="1177"
    )

    card = detail(harness.bank.cards()[2])
    shown = render("card_status", language, {"card": card}).split(" ", 1)[1]
    assert reply(events).startswith(shown.split(" ")[0].capitalize())
    assert tools(harness) == ["list_cards", "get_card"]
    assert decisions(chat) == [
        {
            "request_label": "card_status",
            "outcome_class": "answer",
            "awaiting": "none",
            "rules": ["POL-01", "POL-21"],
            "pending_labels": [],
        }
    ]
    assert checks(chat)[0]["passed"] is True
    # The model reads the answer's shape beside its placeholders, so it writes sentences around them, not a list.
    system = harness.script.model_inputs["reply"][-1][0]
    assert f"Shape: {SHAPES['card_status']}" in json.dumps(system.content)


@pytest.mark.parametrize(
    ("language", "text", "expected"),
    [
        (
            "es",
            "¿Qué tarjetas tengo?",
            "Esta es su tarjeta y su estado:\n\n"
            "- Tarjeta de débito terminada en 1177: activa; fecha de vencimiento: no registrada",
        ),
        (
            "pt",
            "Quais cartões eu tenho?",
            "Este é o seu cartão e o status dele:\n\n"
            "- Cartão de débito final 1177: ativo; validade: não registrada",
        ),
    ],
)
def test_an_only_card_asked_about_as_the_customers_cards_is_listed(
    harness: Harness, language: str, text: str, expected: str
) -> None:
    # Listed as each of several is (POL-14), so the chat draws it in a frame (ADR-0007). The model's lead holds a
    # digit here, so the fixed one shows.
    harness.bank.listed["cards"] = [harness.bank.listed["cards"][2]]
    chat = Chat(harness)
    harness.script.replies = ["Tiene 1 tarjeta:\n\n{cards}"]

    events = chat.say(text, requests=["card_status"], language=language, cards="all")

    assert reply(events) == expected
    assert decisions(chat)[0]["rules"] == ["POL-01", "POL-21", "POL-14"]
    system = harness.script.model_inputs["reply"][-1][0]
    assert f"Shape: {SHAPES['only_card_status']}" in json.dumps(system.content)


def test_several_cards_and_none_named_are_asked_about_then_answered(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    harness.script.replies = ["Su {card} está {card.status} ({card.expiration})."]

    asked = chat.say("¿En qué estado está mi tarjeta?", requests=["card_status"])

    assert reply(asked).startswith(FIXED["which_card_read"]["es"].split("\n")[0])
    assert reply(asked).split("\n\n", 1)[1] == "\n".join(
        [
            "- Tarjeta de crédito terminada en 4821",
            "- Tarjeta de crédito terminada en 9034",
            "- Tarjeta de débito terminada en 1177",
        ]
    )
    assert decisions(chat)[0]["awaiting"] == "card"
    assert decisions(chat)[0]["outcome_class"] == "clarify"

    answered = chat.say("La de débito", requests=["card_status"], card_type="debit")

    assert (
        reply(answered)
        == "Su tarjeta de débito terminada en 1177 está activa (no registrada)."
    )
    assert decisions(chat)[0]["outcome_class"] == "answer"
    # The model answers the question that made the request, not the answer to which card.
    request = harness.script.model_inputs["reply"][-1][1]
    assert request.content == "¿En qué estado está mi tarjeta?"


def test_a_new_request_while_which_card_is_asked_ends_the_question(
    harness: Harness,
) -> None:
    # POL-06, version 6: the message doesn't answer which card, so it is served as the request it holds.
    chat = Chat(harness)
    chat.say("¿En qué estado está mi tarjeta?", requests=["card_status"])
    harness.script.replies = ["{card}, {window.from} - {window.to}:\n\n{transactions}"]

    events = chat.say(
        "Mejor, ¿cuáles son los movimientos de la 4821?",
        requests=["recent_transactions"],
        question="unanswered",
        last_four="4821",
    )

    assert reply(events).startswith(f"{CARD.capitalize()}, ")
    assert decisions(chat) == [
        {
            "request_label": "recent_transactions",
            "outcome_class": "answer",
            "awaiting": "none",
            "rules": ["POL-19", "POL-25"],
            "pending_labels": [],
        }
    ]


def test_a_request_that_names_no_card_is_about_the_card_last_settled_on(
    harness: Harness,
) -> None:
    # POL-13, version 3: the debit card was just read, so "its transactions" are its, with no question asked.
    chat = Chat(harness)
    harness.script.replies = ["{card}: {card.status}, {card.expiration}."]
    chat.say("¿Cómo está mi débito 1177?", requests=["card_status"], last_four="1177")

    events = chat.say("¿Y sus movimientos recientes?", requests=["recent_transactions"])

    card = harness.bank.cards()[2]
    assert reply(events).startswith(
        render(
            "transactions_none",
            "es",
            {"card": card, "window": harness.bank.window["window"]},
        )
    )
    assert decisions(chat)[0]["outcome_class"] == "answer"


def test_asking_about_all_the_cards_drops_the_card_last_settled_on(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    harness.script.replies = ["{card}: {card.status}, {card.expiration}."]
    chat.say("¿Cómo está mi débito 1177?", requests=["card_status"], last_four="1177")
    harness.script.replies = ["Estas son sus tarjetas:\n\n{cards}"]
    chat.say("¿Cuáles son mis tarjetas?", requests=["card_status"], cards="all")

    asked = chat.say("¿Y sus movimientos recientes?", requests=["recent_transactions"])

    assert reply(asked).startswith(FIXED["which_card_read"]["es"].split("\n")[0])
    assert decisions(chat)[0]["awaiting"] == "card"


def test_a_bare_four_digit_number_names_the_card_without_the_model(
    harness: Harness,
) -> None:
    # The extractor returns nothing; code reads "en 4821" as the card that ends in it (POL-13).
    chat = Chat(harness)
    harness.script.replies = ["{card}, {window.from} - {window.to}:\n\n{transactions}"]

    events = chat.say(
        "¿Cuáles son mis movimientos recientes en 4821?",
        requests=["recent_transactions"],
    )

    assert reply(events).startswith(f"{CARD.capitalize()}, ")
    assert tools(harness) == ["list_cards", "find_transactions"]
    assert decisions(chat)[0]["outcome_class"] == "answer"


def test_an_amounts_digits_dont_name_a_card(harness: Harness) -> None:
    # 1177 ends the debit card, but here it is an amount's whole part.
    chat = Chat(harness)
    harness.script.replies = ["Su {card} está {card.status} ({card.expiration})."]

    asked = chat.say(
        "Pagué 1177.00 con mi tarjeta, ¿en qué estado está?", requests=["card_status"]
    )

    assert reply(asked).startswith(FIXED["which_card_read"]["es"].split("\n")[0])


def test_a_bare_number_that_ends_none_of_the_cards_still_asks_which(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    harness.script.replies = ["Su {card} está {card.status} ({card.expiration})."]

    asked = chat.say("¿En qué estado está mi tarjeta 2054?", requests=["card_status"])

    assert reply(asked).startswith(FIXED["which_card_read"]["es"].split("\n")[0])

    answered = chat.say("La 1177.", requests=["card_status"])

    assert reply(answered).startswith("Su tarjeta de débito terminada en 1177 ")
    assert decisions(chat)[0]["outcome_class"] == "answer"


def test_a_reply_call_that_fails_keeps_what_was_said_before_it(
    harness: Harness,
) -> None:
    # POL-36: the reply after a confirmation a new request ended says the card wasn't blocked; POL-48 follows it.
    chat = Chat(harness)
    chat.say("Bloquear la 1177 por pérdida", last_four="1177", block_reason="lost")
    harness.script.reply_error = RuntimeError("provider down")

    events = chat.say(
        "¿Cuáles son mis tarjetas?", requests=["card_status"], cards="all"
    )

    assert reply(events).split("\n\n") == [
        render("confirmation_lapsed", "es", {"card": harness.bank.cards()[2]}),
        FIXED["unavailable"]["es"],
        FIXED["handoff_offer"]["es"],
    ]
    assert offered(events) == "tool_failure"
    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["awaiting"]) == (
        "abstain",
        "handoff_control",
    )


def test_a_customer_who_asks_which_conflicting_fact_is_right_is_offered_a_person(
    harness: Harness,
) -> None:
    # POL-30, POL-31: both facts stated, neither chosen.
    chat = Chat(harness)
    harness.script.replies = ["Su {card} está {card.status}; vence {card.expiration}."]

    events = chat.say(
        "¿Mi tarjeta 4821 sigue sirviendo?",
        requests=["card_status"],
        last_four="4821",
        conflict="asks_which",
    )

    text = reply(events)
    assert FIXED["past_expiration"]["es"].format(card=CARD) in text
    assert FIXED["conflict_unresolved"]["es"] in text
    assert offered(events) == "record_conflict"
    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["awaiting"]) == (
        "answer",
        "handoff_control",
    )
    assert {"POL-30", "POL-31"} <= set(decided["rules"])


@pytest.mark.parametrize("label", ["card_status", "available_credit", "block_card"])
def test_someone_elses_card_is_refused_without_reading_it(
    harness: Harness, label: str
) -> None:
    # POL-08.
    chat = Chat(harness)

    events = chat.say(
        "Quiero saber de la tarjeta de mi esposa.",
        requests=[label],
        owner="someone_else",
    )

    assert reply(events) == FIXED["refused"]["es"]
    assert tools(harness) == ["list_cards"]
    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["rules"]) == ("decline", ["POL-08"])


def test_a_later_message_reads_the_cards_the_chat_last_listed_in_their_order(
    harness: Harness,
) -> None:
    # POL-13: "la segunda" names the card at that place in the list the chat showed, which the first message has none of.
    chat = Chat(harness)
    harness.script.replies = ["Estas son sus tarjetas:\n\n{cards}"]
    chat.say("¿Qué tarjetas tengo?", requests=["card_status"], cards="all")
    chat.say(
        "¿Y cuánto crédito tiene la segunda?",
        requests=["available_credit"],
        last_four="9034",
    )

    assert EARLIER not in extracted(harness, 0)
    assert extracted(harness, 1).endswith(
        f"{EARLIER}\n- credit card ending in 4821\n- credit card ending in 9034\n- debit card ending in 1177"
    )


def test_a_question_that_lists_the_cards_is_the_list_a_later_message_reads(
    harness: Harness,
) -> None:
    # A new request in place of the answer still names a card by its place in the question's list.
    chat = Chat(harness)
    chat.say("¿Cuánto crédito tengo?", requests=["available_credit"])
    harness.script.replies = ["{card}: {card.status}, {card.expiration}."]
    chat.say(
        "Mejor dígame cómo está la segunda.",
        requests=["card_status"],
        question="unanswered",
        last_four="9034",
    )

    # The answer is read against the question, and the request it holds against the list.
    assert "answering which of these cards" in extracted(harness, 1)
    assert extracted(harness, -1).endswith(
        f"{EARLIER}\n- credit card ending in 4821\n- credit card ending in 9034"
    )


# Available credit


@pytest.mark.parametrize(
    ("language", "figure"), [("es", "3,759.45 USD"), ("pt", "3,759.45 USD")]
)
def test_an_active_credit_card_gets_the_figure_the_tool_computed(
    harness: Harness, language: str, figure: str
) -> None:
    credit(
        harness,
        "available",
        credit_limit=5000.0,
        current_balance=1240.55,
        available_credit=3759.45,
        over_limit_by=0,
    )
    harness.script.replies = ["Al {as_of}, su {card} tiene {credit.available}."]
    chat = Chat(harness)

    events = chat.say(
        "¿Cuánto crédito tengo?", requests=["available_credit"], last_four="4821"
    )

    text = reply(events)
    assert f"Al 17/06/2026, su {CARD} tiene {figure}." in text
    # POL-31: the card is Active and past its expiration, so both facts follow.
    assert (
        FIXED["past_expiration"][language if language == "es" else "es"].split(" ")[0]
        in text
    )
    assert tools(harness) == ["list_cards", "get_available_credit"]
    (decided,) = decisions(chat)
    assert decided["outcome_class"] == "answer"
    assert {"POL-01", "POL-19", "POL-22", "POL-31"} <= set(decided["rules"])


def test_a_balance_over_the_limit_is_no_credit_and_the_amount_over(
    harness: Harness,
) -> None:
    # POL-23.
    credit(
        harness,
        "over_limit",
        credit_limit=2000.0,
        current_balance=2150.4,
        available_credit=0,
        over_limit_by=150.4,
    )
    chat = Chat(harness)
    harness.script.replies = [
        "Al {as_of}, su {card} no tiene crédito: lo supera en {credit.over_by}."
    ]

    events = chat.say("¿Crédito?", requests=["available_credit"], last_four="4821")

    assert "lo supera en 150.40 USD" in reply(events)
    assert "POL-23" in decisions(chat)[0]["rules"]


@pytest.mark.parametrize(
    ("last_four", "name"),
    [("1177", "credit_debit_card"), ("9034", "credit_not_active")],
)
def test_a_debit_card_or_one_not_active_is_declined_in_fixed_text(
    harness: Harness, last_four: str, name: str
) -> None:
    # POL-22.
    chat = Chat(harness)

    events = chat.say("¿Crédito?", requests=["available_credit"], last_four=last_four)

    assert reply(events).startswith(FIXED[name]["es"].split("{card}")[0])
    assert harness.script.model_inputs["reply"] == []
    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["rules"]) == ("decline", ["POL-22"])


def test_a_missing_limit_is_abstained_on_with_a_person_offered(
    harness: Harness,
) -> None:
    # POL-24: never read as zero or unlimited.
    chat = Chat(harness)

    events = chat.say("¿Crédito?", requests=["available_credit"], last_four="4821")

    assert reply(events).startswith(
        render("credit_no_limit", "es", {"card": harness.bank.cards()[0]})
    )
    assert offered(events) == "missing_data"
    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["awaiting"]) == (
        "abstain",
        "handoff_control",
    )


def test_credit_cards_are_meant_among_several_that_fit(harness: Harness) -> None:
    # POL-13: two credit cards and a debit one, none named, so the credit ones are listed.
    chat = Chat(harness)

    events = chat.say("¿Cuánto crédito tengo?", requests=["available_credit"])

    assert reply(events).endswith(
        "- Tarjeta de crédito terminada en 4821\n- Tarjeta de crédito terminada en 9034"
    )


def test_all_credit_cards_are_answered_for_each(harness: Harness) -> None:
    # POL-14 (version 2).
    chat = Chat(harness)

    events = chat.say(
        "¿Y el crédito de todas mis tarjetas?",
        requests=["available_credit"],
        cards="all",
    )

    assert tools(harness) == [
        "list_cards",
        "get_available_credit",
        "get_available_credit",
    ]
    assert offered(events) == "missing_data"
    (decided,) = decisions(chat)
    assert decided["outcome_class"] == "abstain"
    assert {"POL-14", "POL-22", "POL-24"} <= set(decided["rules"])


def over_limit(harness: Harness) -> None:
    # The closed credit card made active, and over its limit.
    harness.bank.listed["cards"][1] = {
        **harness.bank.listed["cards"][1],
        "product_status": "Active",
    }
    harness.bank.credit[CLOSED]["card"] = {
        **harness.bank.credit[CLOSED]["card"],
        "product_status": "Active",
        "availability": "over_limit",
        "credit_limit": 2000.0,
        "current_balance": 2150.4,
        "available_credit": 0,
        "over_limit_by": 150.4,
    }


FIXED_CREDITS = "Al 17/06/2026, este es el crédito disponible de sus tarjetas:"


@pytest.mark.parametrize(
    ("written", "opening"),
    [
        (
            "Al {as_of}, este es el crédito de sus tarjetas:\n\n{credits}",
            "Al 17/06/2026, este es el crédito de sus tarjetas:",
        ),
        # Refused by the check, so the fixed reply states the list: inside the sentence, and once per card.
        ("Al {as_of}, sus tarjetas tienen {credits}.", FIXED_CREDITS),
        (
            "Al {as_of}, este es el crédito de sus tarjetas:\n\n{credits}\n{credits}",
            FIXED_CREDITS,
        ),
    ],
)
def test_the_credit_of_several_cards_is_one_list_under_one_sentence(
    harness: Harness, written: str, opening: str
) -> None:
    # POL-14: the cards with a figure in one answer, which the model writes once.
    credit(
        harness,
        "available",
        credit_limit=5000.0,
        current_balance=1240.55,
        available_credit=3759.45,
        over_limit_by=0,
    )
    over_limit(harness)
    harness.script.replies = [written]
    chat = Chat(harness)

    events = chat.say(
        "¿Y el crédito de todas mis tarjetas?",
        requests=["available_credit"],
        cards="all",
    )

    assert reply(events).startswith(
        f"{opening}\n\n"
        "- Tarjeta de crédito terminada en 4821: 3,759.45 USD\n"
        "- Tarjeta de crédito terminada en 9034: sin crédito disponible; supera el límite en 150.40 USD\n\n"
    )
    assert len(harness.script.model_inputs["reply"]) == 1
    (checked,) = checks(chat)
    assert checked["fell_back"] is (opening == FIXED_CREDITS)
    (decided,) = decisions(chat)
    assert decided["outcome_class"] == "answer"
    assert {"POL-14", "POL-22", "POL-23", "POL-31"} <= set(decided["rules"])


def test_one_card_with_a_figure_among_several_keeps_its_sentence(
    harness: Harness,
) -> None:
    credit(
        harness,
        "available",
        credit_limit=5000.0,
        current_balance=1240.55,
        available_credit=3759.45,
        over_limit_by=0,
    )
    harness.script.replies = ["Al {as_of}, su {card} tiene {credit.available}."]
    chat = Chat(harness)

    events = chat.say(
        "¿Y el crédito de todas mis tarjetas?",
        requests=["available_credit"],
        cards="all",
    )

    text = reply(events)
    assert text.startswith(f"Al 17/06/2026, su {CARD} tiene 3,759.45 USD.\n\n")
    assert render("credit_not_active", "es", {"card": harness.bank.cards()[1]}) in text


def test_a_customer_the_tool_doesnt_serve_in_full_is_handed_off(
    harness: Harness,
) -> None:
    # POL-12: the tool's own refusal, should the listing have said otherwise.
    def refusing(request: Any) -> Any:
        if b"get_available_credit" in request.content:
            listed = harness.bank.listed
            return tool_result(
                {
                    "outcome": "refused",
                    "stamp": listed["stamp"],
                    "clock": listed["clock"],
                    "refusal": "not_served",
                }
            )
        return harness.bank.answer(request)

    harness.script.gateway = refusing
    chat = Chat(harness)

    chat.say("¿Crédito?", requests=["available_credit"], last_four="4821")

    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["rules"][0]) == ("hand_off", "POL-12")


# Recent transactions


def window(harness: Harness, cursor: str | None) -> None:
    harness.bank.window["next_cursor"] = cursor


@pytest.mark.parametrize("language", ["es", "pt"])
def test_a_page_lists_the_window_newest_first_with_its_dates(
    harness: Harness, language: str
) -> None:
    chat = Chat(harness)
    harness.script.replies = ["{card}, {window.from} - {window.to}:\n\n{transactions}"]
    text = {"es": "Mis movimientos de la 4821", "pt": "Minhas transações do 4821"}

    events = chat.say(
        text[language],
        requests=["recent_transactions"],
        language=language,
        last_four="4821",
    )

    lines = reply(events).split("\n\n")[1].split("\n")
    assert reply(events).startswith(
        f"{CARD.capitalize() if language == 'es' else 'Cartão de crédito final 4821'}, 20/03/2026 06:00 - 18/06/2026 06:00:"
    )
    # POL-25: the country only abroad, the merchant only for a purchase, "not recorded" when it's missing.
    assert lines[0].endswith(" · USA")
    assert "189.90 USD" in lines[0]
    assert (
        "comercio no registrado"
        if language == "es"
        else "estabelecimento não registrado"
    ) in lines[1]
    assert "Servicio Ejemplo" not in lines[2]
    assert FIXED["transactions_more"][language] not in reply(events)
    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["rules"]) == (
        "answer",
        ["POL-19", "POL-25"],
    )


def test_the_next_ten_follow_the_cursor_and_then_none_are_left(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    window(harness, "2026-05-30 11:52:04#TRX-EXAMPLE0000000000005")
    harness.script.replies = ["{card} {window.from} {window.to}\n\n{transactions}"]

    first = chat.say(
        "Mis movimientos", requests=["recent_transactions"], last_four="4821"
    )

    assert reply(first).endswith(FIXED["transactions_more"]["es"])
    window(harness, None)
    following = chat.say(
        "¿Y los siguientes?", requests=["recent_transactions"], page="next"
    )

    sent = harness.script.tool_calls[-1]["params"]["arguments"]
    assert sent["cursor"] == "2026-05-30 11:52:04#TRX-EXAMPLE0000000000005"
    assert FIXED["transactions_more"]["es"] not in reply(following)
    # The router read what the reply before it offered.
    routed: Any = harness.script.model_inputs["route"][-1][0].content
    assert "next 10" in routed[1]["text"]
    calls = len(harness.script.tool_calls)

    none_left = chat.say("¿Más?", requests=["recent_transactions"], page="next")

    assert reply(none_left).startswith("No hay más movimientos de su")
    assert len(harness.script.tool_calls) == calls + 1  # list_cards only


def test_a_card_with_none_in_the_window_gets_that_answer_with_its_dates(
    harness: Harness,
) -> None:
    chat = Chat(harness)

    events = chat.say(
        "Movimientos del débito", requests=["recent_transactions"], last_four="1177"
    )

    assert reply(events) == (
        "Su tarjeta de débito terminada en 1177 no tiene movimientos entre el "
        "20/03/2026 06:00 y el 18/06/2026 06:00."
    )


def test_an_earlier_period_is_declined_stating_the_window(harness: Harness) -> None:
    chat = Chat(harness)

    events = chat.say(
        "Quiero mis movimientos del año pasado",
        requests=["recent_transactions"],
        page="earlier",
    )

    assert reply(events) == (
        "Solo puedo mostrarle los movimientos de los últimos 90 días, entre el "
        "20/03/2026 06:00 y el 18/06/2026 06:00."
    )
    assert tools(harness) == ["list_cards"]
    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["rules"]) == ("decline", ["POL-25"])


# A decline explained


def declined(harness: Harness, **changed: Any) -> None:
    harness.bank.window["transactions"][1].update(changed)


@pytest.mark.parametrize("language", ["es", "pt"])
def test_a_decline_found_is_explained_by_its_codes_meaning(
    harness: Harness, language: str
) -> None:
    chat = Chat(harness)
    harness.script.fitting = [2]
    harness.script.replies = ["{card}:\n\n{transaction}\n{transaction.meaning}"]
    text = {
        "es": "¿Por qué rechazaron mi pago?",
        "pt": "Por que meu pagamento foi recusado?",
    }

    events = chat.say(
        text[language], requests=["decline_reason"], language=language, last_four="4821"
    )

    # The reason on the line under its transaction, so the two read as one list, and without the code's number.
    reason = {
        "es": "- Motivo: fondos insuficientes",
        "pt": "- Motivo: saldo insuficiente",
    }
    assert reply(events).endswith(f"\n{reason[language]}")
    assert checks(chat)[0]["passed"] is True
    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["rules"]) == (
        "answer",
        ["POL-27", "POL-02", "POL-29"],
    )


def test_among_several_that_fit_the_declined_one_is_meant(harness: Harness) -> None:
    # POL-27 (version 2).
    chat = Chat(harness)
    harness.script.fitting = [1, 2, 3]
    harness.script.replies = ["{card}, {transaction}: {transaction.meaning}."]

    events = chat.say(
        "¿Por qué me rechazaron?", requests=["decline_reason"], last_four="4821"
    )

    assert "fondos insuficientes" in reply(events)
    assert decisions(chat)[0]["outcome_class"] == "answer"


def test_a_pending_one_is_reported_by_its_status_without_its_code(
    harness: Harness,
) -> None:
    # POL-28.
    chat = Chat(harness)
    harness.script.fitting = [3]
    harness.script.replies = [
        "{card}:\n{transaction}\nfigura como {transaction.status}."
    ]

    events = chat.say(
        "Me rechazaron un pago", requests=["decline_reason"], last_four="4821"
    )

    assert reply(events).endswith("figura como pendiente.")
    assert "código" not in reply(events)
    assert "POL-28" in decisions(chat)[0]["rules"]


def test_a_decline_with_no_listed_code_is_abstained_on_with_a_person_offered(
    harness: Harness,
) -> None:
    # POL-32.
    declined(harness, response_code="99", response_meaning=None)
    chat = Chat(harness)
    harness.script.fitting = [2]

    events = chat.say("¿Por qué?", requests=["decline_reason"], last_four="4821")

    assert FIXED["decline_no_code"]["es"].split("\n\n")[-1] in reply(events)
    assert offered(events) == "missing_data"
    assert harness.script.model_inputs["reply"] == []
    (decided,) = decisions(chat)
    assert decided["outcome_class"] == "abstain"
    assert {"POL-02", "POL-32"} <= set(decided["rules"])


def test_several_declines_are_listed_and_asked_about(harness: Harness) -> None:
    declined_twice = copy.deepcopy(harness.bank.window["transactions"][1])
    declined_twice["transaction_id"] = "TRX-EXAMPLE0000000000009"
    harness.bank.window["transactions"].append(declined_twice)
    chat = Chat(harness)
    harness.script.fitting = [1, 2, 3, 4]

    events = chat.say(
        "¿Por qué me rechazaron?", requests=["decline_reason"], last_four="4821"
    )

    assert reply(events).startswith(FIXED["which_decline"]["es"].split("{card}")[0])
    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["awaiting"]) == ("clarify", "transaction")


def test_no_decline_that_fits_is_said_so(harness: Harness) -> None:
    chat = Chat(harness)
    harness.script.fitting = []

    events = chat.say(
        "¿Por qué me rechazaron ayer?", requests=["decline_reason"], last_four="4821"
    )

    assert reply(events) == render(
        "decline_not_found", "es", {"card": harness.bank.cards()[0]}
    )


def test_code_54_before_the_recorded_expiration_states_both_facts(
    harness: Harness,
) -> None:
    # POL-30: the card's expiration comes from get_card.
    declined(
        harness,
        response_code="54",
        response_meaning="expired_card",
        after_card_expiration=False,
    )
    chat = Chat(harness)
    harness.script.fitting = [2]
    harness.script.replies = ["{card}, {transaction}: {transaction.meaning}."]

    events = chat.say("¿Por qué?", requests=["decline_reason"], last_four="4821")

    assert "anterior a la fecha de vencimiento registrada" in reply(events)
    assert "02/2026" in reply(events)
    assert tools(harness)[-1] == "get_card"
    assert "POL-30" in decisions(chat)[0]["rules"]


# What the chat doesn't serve


def test_an_unblock_is_handed_off(harness: Harness) -> None:
    # POL-41.
    chat = Chat(harness)

    events = chat.say(
        "Desbloqueen mi tarjeta 4821",
        requests=["unsupported"],
        service="unblock",
        last_four="4821",
    )

    assert reply(events).startswith(FIXED["unblock_by_person"]["es"])
    (case,) = cases(harness)
    assert case["payload"]["reason_code"] == "unblock_request"
    assert case["payload"]["trigger"] == "required"
    (decided,) = decisions(chat)
    assert decided["outcome_class"] == "hand_off"


@pytest.mark.parametrize(
    "service", ["replacement", "pin", "limit_increase", "other_card_service"]
)
def test_a_card_service_the_chat_doesnt_serve_is_declined_with_a_person_offered(
    harness: Harness, service: str
) -> None:
    # POL-42 (version 2).
    chat = Chat(harness)

    events = chat.say(
        "Quiero cambiar mi PIN", requests=["unsupported"], service=service
    )

    assert offered(events) == "unsupported_request"
    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["awaiting"], decided["rules"]) == (
        "decline",
        "handoff_control",
        ["POL-42"],
    )


def test_a_request_outside_cards_is_declined_without_a_person(harness: Harness) -> None:
    # POL-43.
    chat = Chat(harness)

    events = chat.say(
        "¿Cuánto debo de mi préstamo?",
        requests=["unsupported"],
        service="outside_cards",
    )

    assert reply(events) == FIXED["outside_cards"]["es"]
    assert events[-1]["outcome"] == {"type": "success"}
    (decided,) = decisions(chat)
    assert (decided["outcome_class"], decided["rules"]) == ("decline", ["POL-43"])


# A third language (POL-51)
