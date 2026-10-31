"""
The Runtime writes a JSON line for each event an alarm reads: the metric, the source, the turn's ID, and enumerated
fields alone, never a customer's text, a record's value, or another identifier; a tool call's line says whether another
attempt follows and whether its failure was planned, and a block that POL-37's reads don't show writes one of its own
(ADR-0004's amendment of 2026-10-01, the alarms as built; OPS-01, OPS-03, EVL-13).
"""

import json
import logging
import re
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from faro.agent import metrics
from faro.agent.metrics import line

from .conftest import Harness, tool_result
from .test_block import Chat, control_shown
from .test_retries import STATUS, failing

ENUM = re.compile(r"^[a-z][a-z_0-9]*$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
FIELDS = {
    "metric",
    "source",
    "turn_id",
    "node",
    "tool",
    "attempt",
    "outcome",
    "error",
    "planned",
    "last",
    "fell_back",
    "failures",
    "reason_code",
    "trigger",
    "status",
    "flagged",
    "latency_ms",
    "code",
    "reason",
}


class Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())

    def of(self, metric: str) -> list[dict[str, Any]]:
        parsed = [json.loads(text) for text in self.lines]
        return [p for p in parsed if p["metric"] == metric]


@pytest.fixture
def lines() -> Iterator[Lines]:
    handler = Lines()
    metrics.logger.addHandler(handler)
    metrics.logger.setLevel(logging.INFO)
    yield handler
    metrics.logger.removeHandler(handler)


def plain(value: Any) -> bool:
    if value is None or isinstance(value, bool | int):
        return True
    if isinstance(value, str):
        return bool(ENUM.match(value) or UUID.match(value))
    if isinstance(value, list):
        return all(isinstance(v, str) and ENUM.match(v) for v in value)
    return False


def test_a_line_is_one_json_object_on_one_line() -> None:
    text = line("turn_closed", "demo", None, outcome="error", latency_ms=12)

    assert "\n" not in text
    assert json.loads(text) == {
        "metric": "turn_closed",
        "source": "demo",
        "turn_id": None,
        "outcome": "error",
        "latency_ms": 12,
    }


def test_every_line_holds_enumerated_fields_and_nothing_a_customer_wrote(
    harness: Harness, lines: Lines
) -> None:
    chat = Chat(harness)
    harness.script.replies = ["{card}: {card.status}, {card.expiration}."]

    chat.say(STATUS, requests=["card_status"], last_four="1177")
    shown = control_shown(chat)
    chat.press("confirm", shown)

    parsed = [json.loads(text) for text in lines.lines]
    assert {p["metric"] for p in parsed} >= {
        "model_call",
        "tool_call",
        "reply_check",
        "turn_closed",
    }
    for each in parsed:
        assert set(each) <= FIELDS, each
        assert all(plain(v) for v in each.values()), each
        assert each["source"] == "demo"
    sent = "\n".join(lines.lines)
    assert "1177" not in sent and chat.who.customer_id not in sent
    assert chat.who.origin_jti not in sent and chat.who.sub not in sent


def test_a_tool_calls_line_says_whether_another_attempt_follows(
    harness: Harness, lines: Lines
) -> None:
    failing(harness, "get_card", 3)

    Chat(harness).say(STATUS, requests=["card_status"], last_four="1177")

    reads = [t for t in lines.of("tool_call") if t["tool"] == "get_card"]
    assert [(t["attempt"], t["outcome"], t["last"]) for t in reads] == [
        (1, "failed", False),
        (2, "failed", False),
        (3, "failed", True),
    ]
    assert {(t["error"], t["planned"]) for t in reads} == {("lambda_error", False)}


def test_a_planned_failure_is_marked_in_its_line(
    harness: Harness, lines: Lines
) -> None:
    def planned() -> httpx.Response:
        return tool_result({"outcome": "fault", "error": "throttled"})

    failing(harness, "get_card", 1, planned)

    Chat(harness).say(STATUS, requests=["card_status"], last_four="1177")

    first, second = (t for t in lines.of("tool_call") if t["tool"] == "get_card")
    assert (first["error"], first["planned"], first["last"]) == (
        "throttled",
        True,
        False,
    )
    assert (second["outcome"], second["planned"], second["last"]) == ("ok", False, True)


@pytest.mark.parametrize(
    ("answer", "planned"),
    [
        (lambda: tool_result({"outcome": "fault", "error": "lambda_error"}), True),
        (None, False),
    ],
)
def test_a_block_the_reads_dont_show_writes_its_own_line(
    harness: Harness,
    lines: Lines,
    answer: Callable[[], httpx.Response] | None,
    planned: bool,
) -> None:
    from .conftest import tool_error

    chat = Chat(harness)
    shown = control_shown(chat)
    failing(harness, "block_card", 3, answer or tool_error)

    chat.press("confirm", shown)

    (blocked,) = lines.of("block_not_verified")
    assert (blocked["reason"], blocked["planned"]) == ("lost", planned)


def test_a_turn_that_fails_closes_in_error_on_its_line(
    harness: Harness, lines: Lines
) -> None:
    harness.records.fail_on = "decision"

    Chat(harness).say(STATUS, requests=["card_status"], last_four="1177")

    (closed,) = lines.of("turn_closed")
    assert closed["outcome"] == "error"
