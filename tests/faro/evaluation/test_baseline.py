"""
The deterministic baseline: its language read by the retired word lists, which the graph applies as it applies the
model's (POL-50, POL-51); its keyword router, which labels in POL-05's order; its extraction by pattern; its choice of a
transaction by match; and its templates, which pass the reply check and the payload's text rules, each through the
agent's own model calls.
"""

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage

from faro.agent.check import failures
from faro.agent.graph import ASKS, instructions, listing, today
from faro.agent.language import settled
from faro.agent.models import Models
from faro.agent.nodes.state import MORE
from faro.agent.payload import context, required, transcript, written
from faro.agent.texts import FIXED, LANGUAGE_NAMES, placeholders
from faro.evaluation import baseline
from faro.policy.handoffs import HANDOFFS


def models() -> tuple[Models, list[dict[str, Any]]]:
    recorded: list[dict[str, Any]] = []

    async def record(kind: str, **fields: Any) -> None:
        recorded.append({"kind": kind, **fields})

    return Models(baseline.factory, record), recorded


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¿Cuál es el estado de mi tarjeta?", "es"),
        ("Hola, quiero bloquear mi tarjeta.", "es"),
        ("Qual é a situação do meu cartão?", "pt"),
        ("Não reconheço uma compra.", "pt"),
        ("Hola, perdí mi cartão, quiero bloquearla.", "es"),
        ("Hello, can you help me with my card?", "other"),
    ],
)
def test_a_message_reads_as_the_language_its_listed_words_lean_to(
    text: str, expected: str
) -> None:
    assert baseline.language(text) == expected


@pytest.mark.parametrize("text", ["ok", "4821", "bloquear", "asesor", "Visa"])
def test_a_message_without_a_lean_is_unclear_and_keeps_the_conversations_language(
    text: str,
) -> None:
    found = baseline.language(text)

    assert found == "unclear"
    assert settled("pt", found) == "pt"
    assert settled("es", found) == "es"


def test_a_third_language_needs_two_of_its_words_and_none_of_ours() -> None:
    assert baseline.language("Hello there") == "unclear"
    assert baseline.language("Hello, mi tarjeta") == "es"


@pytest.mark.parametrize(
    ("text", "requests"),
    [
        ("Perdí mi tarjeta, bloquéela por favor.", ["block_card"]),
        ("Roubaram meu cartão de crédito, preciso bloquear.", ["block_card"]),
        ("Hay un cargo en mi tarjeta que no reconozco.", ["unrecognized_charge"]),
        ("Quero falar com um atendente.", ["talk_to_human"]),
        ("¿Por qué rechazaron mi tarjeta?", ["decline_reason"]),
        ("Meu cartão está ativo?", ["card_status"]),
        ("¿Cuánto cupo me queda en la tarjeta de crédito?", ["available_credit"]),
        ("Quais são minhas últimas movimentações?", ["recent_transactions"]),
        ("Quiero cambiar el PIN de mi tarjeta.", ["unsupported"]),
    ],
)
def test_a_message_is_labeled_by_the_lists_its_words_are_in(
    text: str, requests: list[str]
) -> None:
    routed = baseline.route(text)

    assert (routed.requests, routed.has_request, routed.complaint) == (
        requests,
        True,
        False,
    )


@pytest.mark.parametrize(
    ("text", "requests"),
    [
        (
            "Quiero ver mis movimientos y saber si la tarjeta está activa.",
            ["card_status", "recent_transactions"],
        ),
        (
            "Bloqueen mi tarjeta y quiero hablar con alguien.",
            ["block_card", "talk_to_human"],
        ),
    ],
)
def test_several_requests_come_in_pol_05s_order(text: str, requests: list[str]) -> None:
    assert baseline.route(text).requests == requests


@pytest.mark.parametrize(
    "text", ["Hola, buenos días.", "Obrigado por tudo.", "¿Qué puedes hacer?", "Ok."]
)
def test_a_message_no_list_matches_holds_no_request(text: str) -> None:
    routed = baseline.route(text)

    assert (routed.requests, routed.has_request) == ([], False)


def test_a_complaint_asks_for_a_person_and_is_marked_a_complaint() -> None:
    routed = baseline.route("Quiero poner una queja por el mal servicio.")

    assert (routed.requests, routed.complaint) == (["talk_to_human"], True)
    assert baseline.route("Quiero hablar con un asesor.").complaint is False


def test_the_next_page_is_a_request_only_after_a_reply_that_offered_it() -> None:
    assert baseline.route("¿Y los siguientes?").has_request is False
    assert baseline.route("¿Y los siguientes?", MORE).requests == [
        "recent_transactions"
    ]


def test_the_router_answers_through_the_agents_model_call_named_at_no_cost() -> None:
    called, recorded = models()

    routed = asyncio.run(called.route("Bloqueio do meu cartão, por favor.", MORE))

    assert routed.requests == ["block_card"]
    assert routed.language == "pt"
    [entry] = recorded
    assert (entry["provider"], entry["model_requested"], entry["model_returned"]) == (
        None,
        "baseline",
        "baseline",
    )
    assert (entry["outcome"], entry["cost_usd"]) == ("ok", 0.0)
    assert entry["output"]["requests"] == ["block_card"]


@pytest.mark.parametrize(
    ("text", "card_type", "last_four"),
    [
        (
            "¿En qué estado está mi tarjeta de débito terminada en 4821?",
            "debit",
            "4821",
        ),
        ("Meu cartão de crédito final 4821 está ativo?", "credit", "4821"),
        ("La de crédito.", "credit", None),
        ("Me refiero a la 4821.", None, "4821"),
        ("Es la de crédito, número 4821.", "credit", "4821"),
        ("¿Cuánto crédito disponible tengo?", None, None),
    ],
)
def test_the_card_is_read_by_its_types_words_and_four_digits_standing_alone(
    text: str, card_type: str | None, last_four: str | None
) -> None:
    details = baseline.extract(text)

    assert (details.card_type, details.last_four) == (card_type, last_four)


@pytest.mark.parametrize(
    "text",
    [
        "No hice la compra del 14/06/2026.",
        "Me cargaron 1177.00 USD en Tienda Ejemplo y yo no fui.",
        "Tengo la 4821 y la 1177, ¿cuál está activa?",
    ],
)
def test_a_dates_year_an_amount_or_two_endings_give_no_last_four(text: str) -> None:
    assert baseline.extract(text).last_four is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Me robaron la tarjeta de crédito, necesito bloquearla.", "stolen"),
        ("Perdi meu cartão, bloqueie por favor.", "lost"),
        ("No la encuentro, creo que se me cayó.", "lost"),
        ("Porque tiene un cobro que no reconozco.", "unrecognized_charge"),
        ("Prefiro não dizer.", "customer_request"),
        ("Quiero bloquear mi tarjeta.", None),
    ],
)
def test_a_blocks_reason_is_read_by_its_words_as_pol_35_codes_it(
    text: str, reason: str | None
) -> None:
    assert baseline.extract(text).details()["block_reason"] == reason


def test_all_the_cards_are_meant_whatever_their_type() -> None:
    details = baseline.extract("Quiero el disponible de todas mis tarjetas de crédito.")

    assert (details.cards, details.card_type) == ("all", None)


@pytest.mark.parametrize(
    ("text", "field", "value"),
    [
        ("Muéstreme los que siguen.", "page", "next"),
        ("Recusaram por cartão vencido. Qual é a verdade?", "conflict", "asks_which"),
        ("Quiero desbloquear mi tarjeta.", "service", "unblock"),
        ("Preciso de uma segunda via do meu cartão.", "service", "replacement"),
        ("Olvidé la clave de mi tarjeta.", "service", "pin"),
        ("Quero um limite maior no meu cartão.", "service", "limit_increase"),
        ("Necesito el estado de cuenta.", "service", "other_card_service"),
        ("Quero saber o saldo da minha conta corrente.", "service", "outside_cards"),
        ("Quiero algo que este chat no hace.", "service", None),
    ],
)
def test_the_other_fields_are_read_by_their_words(
    text: str, field: str, value: str | None
) -> None:
    assert getattr(baseline.extract(text), field) == value


TRANSACTIONS = [
    {
        "transaction_date": "2026-06-16 09:12:00",
        "transaction_type": "Purchase",
        "amount": 25.0,
        "currency": "USD",
        "merchant_name": "Mercado Central",
        "transaction_status": "Approved",
        "transaction_country": "Colombia",
    },
    {
        "transaction_date": "2026-06-14 21:07:33",
        "transaction_type": "Purchase",
        "amount": 1189.9,
        "currency": "USD",
        "merchant_name": "Tienda Ejemplo",
        "transaction_status": "Approved",
        "transaction_country": "Colombia",
    },
    {
        "transaction_date": "2026-06-14 08:00:00",
        "transaction_type": "Purchase",
        "amount": 25.0,
        "currency": "USD",
        "merchant_name": None,
        "transaction_status": "Declined",
        "transaction_country": "Colombia",
    },
]
LISTING = "\n".join(
    [
        "The customer reports a charge they don't recognize.",
        today("2026-06-17"),
        listing(TRANSACTIONS),
    ]
)


@pytest.mark.parametrize(
    ("text", "fitting"),
    [
        ("La de Tienda Ejemplo.", [2]),
        ("El pago por 25.00 USD.", [1, 3]),
        ("Me cargaron 1,189.90 USD y no fui yo.", [2]),
        ("O pagamento de 1.189,90 USD.", [2]),
        ("La del 14/06/2026.", [2, 3]),
        ("Ayer me cobraron algo que no reconozco.", [1]),
        ("Anteontem alguém comprou com o meu cartão.", []),
        ("Mercado Central me cobró 25.00 USD sin que yo comprara nada.", [1]),
        ("La más reciente.", [1]),
        ("No lo sé.", [1, 2, 3]),
        ("La de Tienda Ejemplo por 25.00 USD.", []),
    ],
)
def test_a_transaction_is_chosen_by_every_amount_date_and_merchant_given(
    text: str, fitting: list[int]
) -> None:
    assert baseline.choose(text, LISTING).fitting == fitting


def test_the_extraction_and_the_choice_answer_through_the_agents_model_calls() -> None:
    called, recorded = models()

    details = asyncio.run(called.extract("Me la robaron.", ASKS["block_card"]))
    chosen = asyncio.run(called.choose("É a compra em Tienda Ejemplo.", LISTING))

    assert details.details()["block_reason"] == "stolen"
    assert (chosen.fitting, chosen.language) == ([2], "pt")
    assert [e["outcome"] for e in recorded] == ["ok", "ok"]
    assert recorded[1]["output"]["extracted"] == {
        "fitting": "2",
        "language": "pt",
        "question": None,
    }


# The reads' fixed replies, by the request whose answer the model writes in their place.
WRITTEN = {
    "card_status": "card_status",
    "cards_status": "card_status",
    "only_card_status": "card_status",
    "credit_available": "available_credit",
    "credit_over_limit": "available_credit",
    "transactions_page": "recent_transactions",
    "transactions_next": "recent_transactions",
    "decline_explained": "decline_reason",
    "decline_status": "decline_reason",
}


@pytest.mark.parametrize("language", ["es", "pt"])
@pytest.mark.parametrize("fixed", sorted(WRITTEN))
def test_each_reads_answer_has_a_template_the_reply_check_passes(
    fixed: str, language: str
) -> None:
    called, recorded = models()
    names = list(dict.fromkeys(placeholders(FIXED[fixed][language])))
    facts = instructions({"messages": []}, WRITTEN[fixed], {"shown": "A card."}, names)

    text = asyncio.run(called.reply("¿Y mi tarjeta?", facts, LANGUAGE_NAMES[language]))

    assert set(placeholders(text)) == set(names)
    assert failures(text, dict.fromkeys(names, "valor")) == []
    assert text != FIXED[fixed][language]
    assert recorded[0]["cost_usd"] == 0.0


def test_a_request_without_a_template_gets_its_placeholders_alone() -> None:
    facts = instructions(
        {"messages": []}, "card_status", {"shown": "A card."}, ["card", "as_of"]
    )

    text = baseline.reply(LANGUAGE_NAMES["es"], facts)

    assert text == "{card}\n\n{as_of}"


def test_a_handoffs_text_states_its_reason_and_quotes_the_customer() -> None:
    called, _ = models()
    request = required("block_lapsed", "block_card", ["POL-38"])
    messages: list[AnyMessage] = [
        HumanMessage("Perdí mi tarjeta, bloquéela."),
        AIMessage("¿Por qué quiere bloquear su tarjeta?"),
        HumanMessage("Se me cayó en la calle."),
    ]

    text = asyncio.run(called.handoff_text(transcript(messages), context(request)))

    assert text.summary == HANDOFFS["block_lapsed"].summary
    assert text.customer_statements == [
        "El cliente escribió: «Perdí mi tarjeta, bloquéela.»",
        "El cliente escribió: «Se me cayó en la calle.»",
    ]
    payload = {
        "request": {"summary": ""},
        "customer_statements": [],
        "unresolved_questions": [],
    }
    assert written(payload, text)["customer_statements"] == text.customer_statements
