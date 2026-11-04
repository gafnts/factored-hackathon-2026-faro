"""
The probe's checks, without a stack: a span holding a message, a reply, or one of its lines is found, as sent or as JSON
escapes it, and the probe fails on anything stored that shouldn't be, or on a turn without its trace (ADR-0004, Card
numbers and Operations).
"""

import json
from typing import Any

import pytest

from .probe import failures, held_text

MESSAGE = "¿Cuáles son mis tarjetas y en qué estado están?"
REPLY = "Estas son sus tarjetas:\n- Crédito ****4821: activa"
CLEAN: dict[str, Any] = {
    "typed_number_in_a_reply": False,
    "stored_digits": False,
    "stored_secrets": {"token": False, "key_shaped": False},
    "turns_traced": "3 of 3",
    "typed_number_in_spans": False,
    "text_in_spans": False,
}


def span(**attributes: str) -> str:
    return json.dumps({"name": "chat", "attributes": attributes})


@pytest.mark.parametrize(
    "stored",
    [
        span(**{"gen_ai.input.messages": MESSAGE}),
        json.dumps(
            {"attributes": {"gen_ai.input.messages": MESSAGE}}, ensure_ascii=False
        ),
        span(**{"gen_ai.output.messages": "- Crédito ****4821: activa"}),
    ],
)
def test_a_span_holding_a_message_a_reply_or_one_of_its_lines_is_found(
    stored: str,
) -> None:
    assert held_text(stored, [MESSAGE, REPLY])


def test_a_span_of_counts_and_names_holds_no_text() -> None:
    stored = span(**{"faro.node": "route", "gen_ai.usage.input_tokens": "1200"})
    assert not held_text(stored, [MESSAGE, REPLY, "****4821"])


def test_a_clean_report_passes() -> None:
    assert failures(CLEAN) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("typed_number_in_a_reply", True),
        ("stored_digits", True),
        ("stored_secrets", {"token": False, "key_shaped": True}),
        ("turns_traced", "2 of 3"),
        ("turns_traced", "0 of 0"),
        ("typed_number_in_spans", True),
        ("text_in_spans", True),
    ],
)
def test_the_probe_fails_on_what_shouldnt_be_stored_or_a_turn_without_its_trace(
    key: str, value: Any
) -> None:
    assert failures(CLEAN | {key: value}) == [key]
