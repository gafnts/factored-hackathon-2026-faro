"""
The Runtime files a handoff through file_handoff's Lambda, off the Gateway: each attempt is one tool_call entry, via
direct, whose input is the handoff's ID alone, never the token or the payload; a failed attempt is tried again under the
same call ID, three attempts in all. And every tool call a node makes stays in the graph's private state with the turn
it is recorded in, so a handoff filed in a later turn can cite it (ADR-0004, Where the tools run, and its amendments of
2026-09-30; OPS-02, OPS-04, OPS-06, SEC-05).
"""

import asyncio
import io
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError, ReadTimeoutError

from faro.agent.filing import Filing
from faro.agent.retries import Retries, no_wait

from .conftest import Harness
from .test_block import THREAD, Chat, control_shown

HANDOFF_ID = "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50"
DRAFTED = {
    "outcome": "ok",
    "status": "draft_saved",
    "handoff_id": HANDOFF_ID,
}


class Client:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []

    def invoke(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if answer == "function_error":
            return {"FunctionError": "Unhandled", "Payload": io.BytesIO(b"{}")}
        return {
            "Payload": io.BytesIO(json.dumps(answer).encode()),
            "ResponseMetadata": {"RequestId": "req-0001"},
        }


def arguments() -> dict[str, Any]:
    return {
        "customer_id": "CLI-EXAMPLE00001",
        "origin_jti": "5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68",
        "call_id": str(uuid.uuid4()),
        "mode": "draft",
        "draft": {
            "handoff_id": HANDOFF_ID,
            "language": "pt",
            "reason_code": "unrecognized_charge",
            "confirmation_id": "7c1e2a94-3b5d-4f08-a6e2-9d4b0c8f1e37",
            "card_id": "PRD-EXAMPLE00002",
            "reason": "unrecognized_charge",
        },
    }


def filed(client: Client) -> tuple[Any, list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []

    async def record(kind: str, **fields: Any) -> None:
        entries.append({"kind": kind, **fields})

    filing = Filing("file-handoff", client, lambda: datetime(2026, 10, 2, tzinfo=UTC))
    retries = Retries(sleep=no_wait, jitter=lambda low, high: high)
    call = asyncio.run(filing.file(arguments(), "the-customers-token", record, retries))
    return call, entries


def test_a_filing_is_one_direct_call_recorded_without_the_token() -> None:
    client = Client(DRAFTED)

    call, entries = filed(client)

    assert (call.outcome, call.result) == ("ok", DRAFTED)
    (entry,) = entries
    assert (entry["via"], entry["attempt"], entry["input"]) == (
        "direct",
        1,
        {"handoff_id": HANDOFF_ID},
    )
    assert entry["request_id"] == "req-0001"
    assert "the-customers-token" not in json.dumps(entries)
    event = json.loads(client.requests[0]["Payload"])
    assert event == {
        "token": "the-customers-token",
        "input": arguments() | {"call_id": event["input"]["call_id"]},
    }
    assert client.requests[0]["InvocationType"] == "RequestResponse"


def test_a_failed_attempt_is_tried_again_under_the_same_call() -> None:
    call, entries = filed(Client("function_error", DRAFTED))

    assert call.outcome == "ok"
    assert [e["attempt"] for e in entries] == [1, 2]
    assert [e["outcome"] for e in entries] == ["failed", "ok"]
    assert len({e["call_id"] for e in entries}) == 1
    assert "wait_ms" not in entries[0]
    assert (entries[1]["wait_ms"], entries[1]["waited_for"]) == (1000, "backoff")


def test_three_failed_attempts_end_the_call_as_failed() -> None:
    throttled = ClientError({"Error": {"Code": "TooManyRequestsException"}}, "Invoke")
    timeout = ReadTimeoutError(endpoint_url="https://lambda.example")
    wrong = {"outcome": "ok", "status": "filed"}

    call, entries = filed(Client(throttled, timeout, wrong))

    assert call.outcome == "failed"
    assert [e["error"]["code"] for e in entries] == [
        "throttled",
        "timeout",
        "lambda_error",
    ]


def test_an_answer_the_tool_gave_is_not_tried_again() -> None:
    refused = {"outcome": "refused", "refusal": "token_invalid"}

    call, entries = filed(Client(refused, DRAFTED))

    assert (call.outcome, len(entries)) == ("refused", 1)


def test_every_tool_call_stays_in_state_with_the_turn_that_recorded_it(
    harness: Harness,
) -> None:
    chat = Chat(harness)

    chat.press("confirm", control_shown(chat))

    state = harness.checkpoint(chat.who, THREAD)
    recorded = {
        e["call_id"]: e
        for e in harness.records.of(chat.who.origin_jti)
        if e["kind"] == "tool_call"
    }
    assert [c["tool"] for c in state["evidence"]] == [
        "list_cards",
        "block_card",
        "get_card",
    ]
    turns = {c["turn"] for c in state["evidence"]}
    assert len(turns) == 2
    for cited in state["evidence"]:
        entry = recorded[cited["call_id"]]
        assert entry["entry_key"].startswith(f"{cited['turn']}#")
        assert (cited["called_at"], cited["result"]) == (
            entry["called_at"],
            entry["result"],
        )
        assert cited["outcome"] == "ok"
