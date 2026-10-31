"""
A tool refuses an input that fails the full contract, naming paths and rules but never values (POL-11; SEC-06).
"""

import json
from importlib.resources import files
from typing import Any

import pytest

from faro.contracts import validator
from faro.tools import MAX_ERRORS, invalid_input

EXAMPLES = files("faro.contracts").joinpath("examples")
CARD_NUMBER = "4123456789014821"


def example(tool: str) -> dict[str, Any]:
    loaded: list[dict[str, Any]] = json.loads(
        EXAMPLES.joinpath(f"tools.{tool}_input.json").read_text(encoding="utf-8")
    )
    return loaded[0]


@pytest.mark.parametrize("tool", ["list_cards", "get_card"])
def test_a_valid_input_passes(tool: str) -> None:
    assert invalid_input(tool, example(tool)) is None


def test_a_card_number_in_a_field_is_refused_without_its_value() -> None:
    arguments = example("get_card") | {"card_id": CARD_NUMBER}

    refused = invalid_input("get_card", arguments)

    assert refused == {
        "outcome": "invalid_input",
        "errors": [{"path": "/card_id", "rule": "pattern"}],
    }
    assert CARD_NUMBER not in json.dumps(refused)


def test_an_unexpected_field_is_refused_at_the_root() -> None:
    arguments = example("list_cards") | {CARD_NUMBER: "x"}

    refused = invalid_input("list_cards", arguments)

    assert refused == {
        "outcome": "invalid_input",
        "errors": [{"path": "", "rule": "additionalProperties"}],
    }


def test_errors_are_sorted_and_deduplicated() -> None:
    refused = invalid_input(
        "get_card", {"customer_id": "cli-1", "origin_jti": "not-a-uuid"}
    )

    assert refused is not None
    assert refused["errors"] == [
        {"path": "", "rule": "required"},
        {"path": "/customer_id", "rule": "pattern"},
        {"path": "/origin_jti", "rule": "format"},
    ]


@pytest.mark.parametrize("arguments", [None, [], "x", 1])
def test_an_input_that_isnt_an_object_is_refused(arguments: Any) -> None:
    refused = invalid_input("list_cards", arguments)

    assert refused == {
        "outcome": "invalid_input",
        "errors": [{"path": "", "rule": "type"}],
    }


@pytest.mark.parametrize("tool", ["list_cards", "get_card"])
def test_every_refusal_is_a_valid_output(tool: str) -> None:
    refused = invalid_input(tool, {"customer_id": CARD_NUMBER, "extra": 1})

    assert refused is not None
    assert len(refused["errors"]) <= MAX_ERRORS
    validator("tools", f"{tool}_output").validate(refused)
