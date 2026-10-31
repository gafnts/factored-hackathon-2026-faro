"""
The reply check refuses a model's answer that names a fact it doesn't have, leaves out one its fixed reply states,
writes a figure of its own, names an internal flag or a status the policy withholds, writes a list inside a line, or
puts a decline's reason apart from its transaction, or leaves any other fact alone on a line, and names each
failure by its rule only (ADR-0004, decision 8, and its amendment of 2026-10-01; POL-11, POL-12, POL-18, POL-40).
"""

import pytest

from faro.agent.check import failures
from faro.contracts import schema

FACTS = {"card": "tarjeta de crédito terminada en 4821", "credit.available": "10 USD"}


def test_an_answer_that_names_every_fact_and_nothing_else_passes() -> None:
    text = "Su {card} tiene {credit.available} de crédito disponible."

    assert failures(text, FACTS) == []


@pytest.mark.parametrize(
    ("text", "failed"),
    [
        ("Su {card} tiene {credit.limit} y {credit.available}.", ["unknown_placeholder"]),
        ("Su {card} tiene {credit.available} {", ["unknown_placeholder"]),
        ("Su {card} tiene crédito.", ["missing_fact"]),
        ("Su {card} tiene {credit.available}, unos 10 dólares.", ["bare_number"]),
        ("Su {card} tiene {credit.available} hace ３ días.", ["bare_number"]),
        ("Su {card} tiene {credit.available}; is_fraud no aplica.", ["internal_flag"]),
        ("Su {card} tiene {credit.available}, aunque su cuenta está suspendida.", ["withheld_status"]),
        ("Seu {card} tem {credit.available}, mas o cliente está inativo.", ["withheld_status"]),
        ("Su {card} tiene crédito disponible:\n\n{credit.available}", ["lone_fact"]),
        ("{card}\n\nTiene {credit.available} de crédito disponible.", ["lone_fact"]),
    ],
)  # fmt: skip
def test_each_failure_is_named_by_its_rule(text: str, failed: list[str]) -> None:
    assert failures(text, FACTS) == failed


FOUND = {
    "card": "tarjeta de crédito terminada en 4821",
    "transaction": "- 14/06/2026 21:07, Tienda, 10 USD",
}


@pytest.mark.parametrize(
    "text",
    [
        "Encontré este cargo en su {card}: {transaction}.",
        "Encontré este cargo en su {card}:\n- {transaction}",
        "Encontré este cargo en su {card}:\n{transaction} Lo revisamos.",
    ],
)  # fmt: skip
def test_a_list_that_shares_its_line_is_refused(text: str) -> None:
    assert failures(text, FOUND) == ["inline_list"]


def test_a_list_on_a_line_of_its_own_passes() -> None:
    assert (
        failures(
            "Encontré este cargo en su {card}:\n\n  {transaction}\n\nLo revisamos.",
            FOUND,
        )
        == []
    )


CREDITS = {
    "as_of": "17/06/2026",
    "credits": "- Tarjeta de crédito terminada en 4821: 10 USD\n- Tarjeta de crédito terminada en 9034: 20 USD",
}


def test_several_cards_credit_stands_on_a_line_of_its_own() -> None:
    assert failures("Al {as_of}, sus tarjetas tienen {credits}.", CREDITS) == [
        "inline_list"
    ]
    assert failures("Al {as_of}, este es su crédito:\n\n{credits}", CREDITS) == []


def test_a_list_written_twice_is_refused() -> None:
    # As the model wrote it once per card, so three cards' credit read as nine rows.
    text = "Al {as_of}, este es el crédito disponible en sus tarjetas:\n\n{credits}\n{credits}\n{credits}"

    assert failures(text, CREDITS) == ["repeated_list"]


EXPLAINED = {**FOUND, "transaction.meaning": "- Motivo: fondos insuficientes"}


@pytest.mark.parametrize(
    "text",
    [
        "Encontré esta transacción en su {card}:\n\n{transaction}\n\n{transaction.meaning}",
        "Encontré esta transacción en su {card}:\n\n{transaction.meaning}\n{transaction}",
    ],
)  # fmt: skip
def test_a_reason_apart_from_its_transaction_is_refused(text: str) -> None:
    assert failures(text, EXPLAINED) == ["reason_apart"]


def test_a_reason_inside_a_sentence_is_refused() -> None:
    text = "Encontré esta transacción en su {card}:\n{transaction}\nFue rechazada por {transaction.meaning}."

    assert failures(text, EXPLAINED) == ["inline_list", "reason_apart"]


def test_a_reason_right_under_its_transaction_passes() -> None:
    text = "Encontré esta transacción en su {card}:\n\n{transaction}\n{transaction.meaning}"

    assert failures(text, EXPLAINED) == []


def test_a_digit_run_is_found_once_the_text_is_filled() -> None:
    # A fact's value is formatted by code, but the run it makes beside another counts all the same (POL-11).
    facts = {"card": "1234 5678", "credit.available": "9012 3456"}

    assert failures("{card} {credit.available}", facts) == ["digit_run"]


def test_the_failures_are_the_execution_records() -> None:
    allowed = schema("execution-record")["$defs"]["reply_check_entry"]["properties"][
        "failures"
    ]["items"]["enum"]

    assert {
        "unknown_placeholder",
        "missing_fact",
        "inline_list",
        "reason_apart",
        "lone_fact",
        "repeated_list",
    } <= set(allowed)
