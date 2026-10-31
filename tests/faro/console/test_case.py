"""
A case is found by its reference, with the calls its evidence names as the execution record holds them and the rows of
their results the case's facts and actions name; a draft, an evaluation's case, and the conversation's text never reach
the console; and each Lambda answers a human agent only, within its contract (ADR-0007, The console API, and its
amendments of 2026-09-30; CTL-05, OPS-02, EVL-13, SEC-05).
"""

import copy
import json
import logging
from importlib.resources import files
from typing import Any

import pytest

from faro.console.access import staff_of
from faro.console.case import CALL, read_case
from faro.console.handlers import answer_case, answer_queue
from faro.console.queue import MemoryQueue
from faro.tools.cases import MemoryCases, reference_item
from faro.tools.provenance import MemoryRecords

from .test_access import STAFF_CLIENT, SUB, event

EXAMPLES = files("faro.contracts").joinpath("examples")
TURNS = (
    "2026-10-02T15:40:47.301Z#3f6b2d8e-1c4a-4e97-b5f0-8a2d6c9e1b73",
    "2026-10-02T15:41:37.102Z#a8e4c1f7-6d2b-4a53-9c8e-2f7b5d1a9e06",
)
SIGN_IN = "5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68"
TEXT = "No reconozco <script>alert(1)</script> en mi tarjeta"


def example(name: str) -> Any:
    return json.loads(EXAMPLES.joinpath(name).read_text(encoding="utf-8"))


def cases() -> list[dict[str, Any]]:
    return [c for c in example("handoff-case.json") if c["kind"] == "case"]


def entry(turn: int, seq: int, kind: str, **fields: Any) -> dict[str, Any]:
    return {
        "sign_in": SIGN_IN,
        "entry_key": f"{TURNS[turn]}#{seq:04d}",
        "turn_id": TURNS[turn].split("#")[1],
        "seq": seq,
        "at": "2026-10-02T15:41:40.000Z",
        "source": "demo",
        "expires_at": 1798991528,
        "kind": kind,
        **fields,
    }


def call(
    turn: int,
    seq: int,
    call_id: str,
    tool: str,
    result: dict[str, Any] | None,
    **fields: Any,
) -> dict[str, Any]:
    recorded = {
        "call_id": call_id,
        "tool": tool,
        "via": "direct" if tool == "file_handoff" else "gateway",
        "attempt": 1,
        "input": {"customer_id": "CLI-EXAMPLE00001", "origin_jti": SIGN_IN},
        "outcome": "ok",
        **fields,
    }
    if result is not None:
        recorded["result"] = result
    return entry(turn, seq, "tool_call", **recorded)


def recorded() -> list[dict[str, Any]]:
    """
    The turns behind the example case: a search that failed once, then the block, its read-back, and the filing, among
    entries that hold the conversation's text.
    """
    block = {
        **example("tools.block_card_output.json")[0],
        "reason": "unrecognized_charge",
    }
    return [
        entry(0, 1, "turn_opened", input={"kind": "message", "text": TEXT}),
        call(
            0,
            2,
            "9d1c4b7e-2f3a-4e58-8b6d-1a0c7e5f3b21",
            "find_transactions",
            None,
            called_at="2026-10-02T15:40:50Z",
            latency_ms=10000,
            request_id=None,
            outcome="failed",
            error={"code": "timeout"},
        ),
        call(
            0,
            3,
            "9d1c4b7e-2f3a-4e58-8b6d-1a0c7e5f3b21",
            "find_transactions",
            example("tools.find_transactions_output.json")[0],
            attempt=2,
            called_at="2026-10-02T15:40:51Z",
            latency_ms=318,
            request_id="gw-3e4f5a6b7c8d",
        ),
        entry(0, 4, "reply", message_id="m1", text=TEXT, language="es", fixed_texts=[]),
        call(
            1,
            1,
            "4e7a2c9d-8b1f-4d36-a5c0-6f2e9b8d1a47",
            "block_card",
            block,
            called_at="2026-10-02T15:41:39Z",
            latency_ms=409,
            request_id="gw-5a6b7c8d9e0f",
        ),
        call(
            1,
            2,
            "c2b8e1f4-7a9d-4c05-9e3b-5d1a8f6c2e90",
            "get_card",
            example("tools.get_card_output.json")[0],
            called_at="2026-10-02T15:41:40Z",
            latency_ms=235,
            request_id="gw-6b7c8d9e0f1a",
        ),
        entry(1, 3, "model_call", node="handoff", output={"summary": TEXT}),
        call(
            1,
            4,
            "71f0d3a6-5c2e-4b89-b7d4-3e9a1c6f8b52",
            "file_handoff",
            example("tools.file_handoff_output.json")[1],
            called_at="2026-10-02T15:42:08Z",
            latency_ms=372,
            request_id="3e4f5a6b-7c8d-4e9f-8a0b-1c2d3e4f5a6b",
        ),
    ]


def book(*held: dict[str, Any]) -> MemoryCases:
    items = [*held]
    for case in held:
        if case["status"] != "draft":
            items.append(
                reference_item(
                    case["reference"], case["handoff_id"], case["expires_at"]
                )
            )
    return MemoryCases(items)


def test_a_case_reads_as_the_console_shows_it() -> None:
    normal, flagged = cases()[1:3]
    details = example("console.case_detail.json")

    assert read_case(book(normal), MemoryRecords(recorded()), "7K2M-9QXA") == (
        200,
        details[0],
    )
    # Filed a moment ago: the Runtime hasn't recorded file_handoff's own call yet.
    assert read_case(book(flagged), MemoryRecords(), "Q4TR-8B2N") == (200, details[1])


def test_only_a_calls_last_attempt_is_shown() -> None:
    _, body = read_case(book(cases()[1]), MemoryRecords(recorded()), "7K2M-9QXA")

    search = body["calls"][0]
    assert (search["attempt"], search["outcome"]) == (2, "ok")


def test_a_failed_call_shows_its_error_and_no_rows() -> None:
    entries = [e for e in recorded() if e.get("attempt") != 2]

    _, body = read_case(book(cases()[1]), MemoryRecords(entries), "7K2M-9QXA")

    search = body["calls"][0]
    assert (search["outcome"], search["error_code"], search["rows"]) == (
        "failed",
        "timeout",
        [],
    )


def test_the_conversations_text_never_reaches_the_console() -> None:
    # Only a tool call's attributes are read, which is all the case Lambda's role may name (CTL-05).
    assert not {"input", "text", "output"} & set(CALL)

    _, body = read_case(book(cases()[1]), MemoryRecords(recorded()), "7K2M-9QXA")

    assert "<script>" not in json.dumps(body)
    assert all("input" not in c for c in body["calls"])


def test_rows_leave_out_what_the_case_doesnt_name() -> None:
    _, body = read_case(book(cases()[1]), MemoryRecords(recorded()), "7K2M-9QXA")

    listed = example("tools.find_transactions_output.json")[0]["transactions"]
    shown = [r["transaction_id"] for r in body["calls"][0]["rows"]]
    assert len(listed) > 1
    assert shown == ["TRX-EXAMPLE0000000000003"]


def test_a_draft_an_evaluations_case_and_an_unknown_reference_read_as_nothing() -> None:
    draft, normal = cases()[:2]
    evaluation = {**copy.deepcopy(normal), "source": "evaluation"}
    elsewhere = {**copy.deepcopy(normal), "reference": "ZZZZ-9999"}
    drafted = MemoryCases([draft, reference_item("7K2M-9QXA", draft["handoff_id"], 0)])

    for held in (drafted, book(evaluation), MemoryCases()):
        assert read_case(held, MemoryRecords(), "7K2M-9QXA") == (
            404,
            {"error": "not_found"},
        )
    moved = MemoryCases(
        [elsewhere, reference_item("7K2M-9QXA", normal["handoff_id"], 0)]
    )
    assert read_case(moved, MemoryRecords(), "7K2M-9QXA")[0] == 404


@pytest.mark.parametrize(
    "reference", ["7k2m-9qxa", "7K2M9QXA", "7K2M-9QXI", "REF#7K2M-9QXA", "", None, 7]
)
def test_a_reference_outside_the_alphabet_is_refused(reference: Any) -> None:
    assert read_case(book(cases()[1]), MemoryRecords(), reference) == (
        400,
        {"error": "invalid_request"},
    )


def stores() -> tuple[MemoryCases, MemoryRecords]:
    return book(cases()[1]), MemoryRecords(recorded())


def asked(**path: Any) -> dict[str, Any]:
    return {**event(), "pathParameters": path}


def test_the_case_lambda_answers_a_human_agent_and_logs_who_read_which_case(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = asked(reference="7K2M-9QXA")

    with caplog.at_level(logging.INFO, logger="faro.console.handlers"):
        response = answer_case(request, stores, staff_of(request, STAFF_CLIENT))

    assert response["statusCode"] == 200
    assert response["headers"]["Cache-Control"] == "no-store"
    assert json.loads(response["body"]) == example("console.case_detail.json")[0]
    (read,) = caplog.records
    assert (read.reference, read.reader) == ("7K2M-9QXA", SUB)  # type: ignore[attr-defined]
    assert "Comercio" not in caplog.text


def test_both_lambdas_refuse_anyone_but_a_human_agent_before_reading() -> None:
    def unread() -> Any:
        raise AssertionError("read before the caller was checked")

    for response in (
        answer_case(asked(reference="7K2M-9QXA"), unread, None),
        answer_queue(
            {"queryStringParameters": {"queue": "dispute_intake"}}, unread, None
        ),
    ):
        assert response["statusCode"] == 403
        assert json.loads(response["body"]) == {"error": "forbidden"}


def test_the_queue_lambda_answers_within_its_contract() -> None:
    request = {**event(), "queryStringParameters": {"queue": "dispute_intake"}}

    response = answer_queue(
        request, lambda: MemoryQueue(), staff_of(request, STAFF_CLIENT)
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["cases"] == []
    refused = answer_queue(
        event(), lambda: MemoryQueue(), staff_of(request, STAFF_CLIENT)
    )
    assert (refused["statusCode"], json.loads(refused["body"])) == (
        400,
        {"error": "invalid_request"},
    )
