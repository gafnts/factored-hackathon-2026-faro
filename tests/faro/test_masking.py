"""
The entrypoint masks a typed card number to its last four digits with the pattern the handoff schema and the execution
record reject, whatever digits or invisible characters spell it (POL-11; SEC-03; ADR-0004, Card numbers).
"""

from collections.abc import Iterator
from typing import Any

import pytest

from faro.contracts import schema
from faro.masking import DIGIT_RUN, has_digit_run, mask
from faro.policy import handoff_schema


def rejected_patterns(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        rejected = node.get("not")
        if isinstance(rejected, dict) and "pattern" in rejected:
            yield rejected["pattern"]
        for value in node.values():
            yield from rejected_patterns(value)
    elif isinstance(node, list):
        for value in node:
            yield from rejected_patterns(value)


def test_the_pattern_is_the_one_the_schemas_reject() -> None:
    found = {
        *rejected_patterns(handoff_schema()),
        *rejected_patterns(schema("execution-record")),
    }

    assert found == {DIGIT_RUN}


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("mi tarjeta 4123456789014821 no pasa", "mi tarjeta ****4821 no pasa"),
        ("4123 4567 8901 4821", "****4821"),
        ("4123-4567-8901-4821", "****4821"),
        ("4123456789012", "****9012"),
        ("4123456789012345678", "****5678"),
        (
            "uno 4123456789014821 y otro 5500000000000004",
            "uno ****4821 y otro ****0004",
        ),
        (
            "\uff14\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19\uff10\uff11\uff14\uff18\uff12\uff11",
            "****4821",
        ),
        ("4123\u200b4567\u200b8901\u200b4821", "****4821"),
        (
            "\u0664\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669\u0660\u0661\u0664\u0668\u0662\u0661",
            "****4821",
        ),
    ],
)
def test_a_typed_card_number_keeps_its_last_four_digits(
    typed: str, stored: str
) -> None:
    assert mask(typed) == stored
    assert not has_digit_run(mask(typed))


@pytest.mark.parametrize(
    "typed",
    [
        "¿Mi tarjeta terminada en 4821 está activa?",
        "Llamé al 555 123 4567 ayer",
        "412345678901",
        "Quais são os meus cartões?",
    ],
)
def test_text_without_a_run_of_13_digits_is_kept(typed: str) -> None:
    assert mask(typed) == typed
    assert not has_digit_run(typed)


@pytest.mark.parametrize(
    "typed",
    [
        "cuatro uno dos tres cuatro cinco seis siete ocho nueve cero uno dos",
        "4123.4567.8901.4821",
        "4123 / 4567 / 8901 / 4821",
    ],
)
def test_the_stated_limits_still_pass(typed: str) -> None:
    assert mask(typed) == typed
