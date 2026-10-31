"""
The read tools' Lambda dispatches on the Gateway's tool name, validates before it reads, and returns only outputs that
fit the contract (ADR-0004, decision 16 and its amendment of 2026-09-29).
"""

from types import SimpleNamespace
from typing import Any

import pytest

from faro.tools import OutputContractError, reads
from faro.tools.reads import answer, handler, tool_name
from faro.tools.sandbox import MemoryOverlay, Stores
from faro.tools.store import MemoryData

from .conftest import OWN, SIGN_IN, example_items

VALID = {"customer_id": OWN, "origin_jti": SIGN_IN, "card_id": "PRD-EXAMPLE00002"}


def context(name: str | None) -> Any:
    if name is None:
        return SimpleNamespace(client_context=None)
    return SimpleNamespace(
        client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": name})
    )


def unopened() -> Stores:
    raise AssertionError("the stores were opened")


def opened(items: list[dict[str, Any]]) -> Stores:
    return Stores(MemoryData(items), MemoryOverlay())


@pytest.mark.parametrize(
    ("name", "tool"),
    [
        ("reads___list_cards", "list_cards"),
        ("reads___get_card", "get_card"),
        ("reads___get_available_credit", "get_available_credit"),
        ("reads___find_transactions", "find_transactions"),
    ],
)
def test_the_tool_comes_from_the_gateways_name(name: str, tool: str) -> None:
    assert tool_name(context(name)) == tool


@pytest.mark.parametrize(
    "name", [None, "", "reads___block_card", "reads___file_handoff", "get_cards"]
)
def test_a_name_that_isnt_a_read_tool_is_refused(name: str | None) -> None:
    with pytest.raises(ValueError, match="not a read tool"):
        tool_name(context(name))


def test_an_invalid_input_is_answered_without_reading() -> None:
    result = answer("get_card", VALID | {"card_id": "4123456789014821"}, unopened)

    assert result == {
        "outcome": "invalid_input",
        "errors": [{"path": "/card_id", "rule": "pattern"}],
    }


def test_the_handler_answers_from_the_tools_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reads, "stores", lambda: opened(example_items()))

    result = handler(VALID, context("reads___get_card"))

    assert result["outcome"] == "ok"
    assert result["card"]["last_four"] == "4821"


def test_an_output_that_doesnt_fit_the_contract_is_never_returned() -> None:
    items = example_items()
    items[1]["customer_status"] = "Unknown"
    items[2]["last_four"] = "44821"

    with pytest.raises(OutputContractError, match="list_cards's output") as raised:
        answer(
            "list_cards",
            {"customer_id": OWN, "origin_jti": SIGN_IN},
            lambda: opened(items),
        )

    assert "44821" not in str(raised.value)
