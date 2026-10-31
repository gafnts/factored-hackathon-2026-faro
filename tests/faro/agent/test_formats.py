"""
Facts are stated as ADR-0004's amendment of 2026-10-01 says, which ADR-0005's oracle reads (POL-19, POL-20, POL-25,
POL-32), and every fixed reply fills from the placeholders the reads leave, in both languages (POL-50).
"""

from typing import Any

import pytest

from faro.agent.check import failures
from faro.agent.formats import (
    amount,
    card_line,
    credit_line,
    day,
    expiration,
    moment,
    transaction_line,
    transaction_name,
)
from faro.agent.texts import FIXED, PLACEHOLDER, fill, render, values

CARD = {
    "product_type": "Tarjeta Crédito",
    "last_four": "4821",
    "product_status": "Active",
    "expiration_date": "2027-03-31",
}
WITHIN = {
    "availability": "available",
    "available_credit": 3759.45,
    "over_limit_by": 0,
    "currency": "USD",
}
OVER = {
    "availability": "over_limit",
    "available_credit": 0,
    "over_limit_by": 150.4,
    "currency": "USD",
}
PURCHASE = {
    "transaction_date": "2026-06-14 21:07:33",
    "transaction_type": "Purchase",
    "transaction_status": "Declined",
    "response_meaning": "insufficient_funds",
    "amount": 1240.5,
    "currency": "COP",
    "merchant_name": "Comercio Ejemplo",
    "transaction_country": "Colombia",
}


@pytest.mark.parametrize(
    ("country", "shown"),
    [
        ("México", "1,240.50 USD"),
        ("Colombia", "1.240,50 USD"),
        ("Argentina", "1.240,50 USD"),
    ],
)
def test_an_amount_is_grouped_as_the_customers_country_writes_it(
    country: str, shown: str
) -> None:
    assert amount(1240.5, "USD", country) == shown


def test_an_amount_keeps_its_cents_and_is_never_rounded_by_float() -> None:
    assert amount(0.1 + 0.2, "ARS", "Argentina") == "0,30 ARS"
    assert amount(2_500_000, "COP", "Colombia") == "2.500.000,00 COP"


def test_dates_read_day_first_to_the_minute() -> None:
    assert day("2026-06-17") == "17/06/2026"
    assert moment("2026-06-14 21:07:33") == "14/06/2026 21:07"


@pytest.mark.parametrize(
    ("language", "missing"), [("es", "no registrada"), ("pt", "não registrada")]
)
def test_an_expiration_is_its_month_and_year_or_not_recorded(
    language: str, missing: str
) -> None:
    # POL-32: never guessed or filled from another field.
    assert expiration("2027-03-31", language) == "03/2027"
    assert expiration(None, language) == missing


def test_a_card_line_names_the_card_its_status_and_its_expiration() -> None:
    assert card_line(CARD, "es") == (
        "- Tarjeta de crédito terminada en 4821: activa; fecha de vencimiento: 03/2027"
    )
    assert card_line({**CARD, "product_status": "Blocked"}, "pt") == (
        "- Cartão de crédito final 4821: bloqueado; validade: 03/2027"
    )


def test_a_credit_line_names_the_card_and_its_credit_or_the_amount_over_its_limit() -> (
    None
):
    assert credit_line(CARD, WITHIN, "es", "Colombia") == (
        "- Tarjeta de crédito terminada en 4821: 3.759,45 USD"
    )
    assert credit_line(CARD, OVER, "pt", "México") == (
        "- Cartão de crédito final 4821: sem crédito disponível; ultrapassa o limite em 150.40 USD"
    )


def test_a_question_lists_the_cards_without_their_statuses_and_the_answer_with_them() -> (
    None
):
    offered = values("es", {"cards": [CARD, {**CARD, "last_four": "1177"}]})

    assert offered["card_list"] == (
        "- Tarjeta de crédito terminada en 4821\n- Tarjeta de crédito terminada en 1177"
    )
    assert "cards" not in offered
    assert fill(FIXED["which_card"]["pt"], values("pt", {"cards": [CARD]})) == (
        "Qual destes cartões você quer bloquear?\n\n- Cartão de crédito final 4821"
    )

    answered = values("pt", {"statuses": [CARD]})

    assert answered["cards"] == (
        "- Cartão de crédito final 4821: ativo; validade: 03/2027"
    )
    assert "card_list" not in answered


def test_a_transaction_shows_its_country_only_abroad_and_its_merchant_only_for_a_purchase() -> (
    None
):
    # POL-25.
    home = transaction_line(PURCHASE, "es", "Colombia")
    abroad = transaction_line(
        {**PURCHASE, "transaction_country": "USA"}, "es", "Colombia"
    )
    withdrawal = transaction_line(
        {**PURCHASE, "transaction_type": "Withdrawal", "merchant_name": None},
        "pt",
        "Colombia",
    )

    assert home == (
        "- 14/06/2026 21:07 · Compra · Comercio Ejemplo · 1.240,50 COP · Rechazada"
    )
    assert abroad.endswith(" · Rechazada · USA")
    assert withdrawal == "- 14/06/2026 21:07 · Saque · 1.240,50 COP · Recusada"


def test_a_missing_merchant_is_not_recorded_and_a_digit_run_in_one_is_masked() -> None:
    # POL-10, POL-11, POL-25: a merchant name is the bank's record text, shown as data.
    missing = transaction_name({**PURCHASE, "merchant_name": None}, "es", "Colombia")
    injected = transaction_name(
        {**PURCHASE, "merchant_name": "Pague a 4123 4567 8901 4821"}, "pt", "Colombia"
    )

    assert "comercio no registrado" in missing
    assert "4123" not in injected and "****4821" in injected


def test_a_transaction_meant_is_named_by_its_merchant_if_a_purchase_and_by_its_type_if_not() -> (
    None
):
    # POL-27, version 6: a payment has no merchant, and "merchant not recorded" read as an unknown shop.
    purchase = transaction_name(PURCHASE, "es", "Colombia")
    payment = transaction_name(
        {**PURCHASE, "transaction_type": "Payment", "merchant_name": None},
        "pt",
        "Colombia",
    )

    assert purchase == "14/06/2026 21:07, Comercio Ejemplo, 1.240,50 COP"
    assert payment == "14/06/2026 21:07, pagamento, 1.240,50 COP"


# Every placeholder a fixed reply names, with a fact that fills it.
SAMPLE: dict[str, Any] = {
    "country": "México",
    "card": CARD,
    "cards": [CARD],
    "statuses": [CARD],
    "reason": "lost",
    "last_four": "4821",
    "reference": "7KQ2-M9TX",
    "transaction": PURCHASE,
    "transactions": [PURCHASE],
    "credit": {"available_credit": 3759.45, "over_limit_by": 0, "currency": "USD"},
    "credits": [{"card": CARD, "credit": WITHIN}, {"card": CARD, "credit": OVER}],
    "as_of": "2026-06-17",
    "window": {"from": "2026-03-20 06:00:00", "to": "2026-06-18 06:00:00"},
    "service": "pin",
    "requests": ["available_credit", "recent_transactions"],
}


@pytest.mark.parametrize("name", sorted(FIXED))
@pytest.mark.parametrize("language", ["es", "pt"])
def test_every_fixed_reply_fills_in_both_languages(name: str, language: str) -> None:
    filled = render(name, language, SAMPLE)

    assert PLACEHOLDER.search(filled) is None
    assert "{" not in filled and "}" not in filled
    assert {"inline_list", "reason_apart", "lone_fact", "repeated_list"}.isdisjoint(
        failures(FIXED[name][language], values(language, SAMPLE))
    )


@pytest.mark.parametrize("name", ["no_request", "greeting", "capabilities"])
@pytest.mark.parametrize("language", ["es", "pt"])
def test_small_talk_before_a_question_asked_again_drops_its_own_question(
    name: str, language: str
) -> None:
    aside = FIXED[f"{name}_aside"][language]

    assert FIXED[name][language].startswith(aside)
    assert aside.endswith(".")
    assert "?" not in aside


def test_a_page_fills_the_transactions_placeholder_with_full_lines() -> None:
    page = values("es", {"page": [PURCHASE], "country": "Colombia"})["transactions"]

    assert page == transaction_line(PURCHASE, "es", "Colombia")


def test_the_transaction_found_stands_as_a_list_of_one() -> None:
    found = values("es", {"transaction": PURCHASE, "country": "Colombia"})[
        "transaction"
    ]

    assert found == f"- {transaction_name(PURCHASE, 'es', 'Colombia')}"


@pytest.mark.parametrize(
    ("text", "filled"),
    [
        ("{card} está activa.", "Tarjeta de crédito terminada en 4821 está activa."),
        ("Hola. {card} está activa.", "Hola. Tarjeta de crédito terminada en 4821 está activa."),
        ("Hola:\n\n{card}", "Hola:\n\nTarjeta de crédito terminada en 4821"),
        ("¿{card} está activa?", "¿Tarjeta de crédito terminada en 4821 está activa?"),
        ("Su {card} está activa.", "Su tarjeta de crédito terminada en 4821 está activa."),
        ("En su {card}: {card}.", "En su tarjeta de crédito terminada en 4821: tarjeta de crédito terminada en 4821."),
    ],
)  # fmt: skip
def test_a_value_that_opens_a_sentence_or_a_line_takes_a_capital(
    text: str, filled: str
) -> None:
    assert fill(text, {"card": "tarjeta de crédito terminada en 4821"}) == filled


def test_the_queue_names_what_is_left_in_words() -> None:
    assert render("queued", "es", SAMPLE) == (
        "Después sigo con su crédito disponible y sus movimientos recientes."
    )
    assert fill("com {requests}", values("pt", {"requests": ["card_status"]})) == (
        "com o status do seu cartão"
    )
