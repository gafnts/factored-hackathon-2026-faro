"""
Decision 18's retries: a model call, a read, and the block are tried again, three attempts at most, each recorded with
the wait before it, after the provider's retry-after or a jittered backoff, and never past the turn's deadline; a
denial is never tried again, and a call that keeps failing ends in POL-48's offer (ADR-0004's amendment of 2026-10-01;
POL-37, POL-48, POL-49; OPS-04, OPS-05, OPS-06).
"""

import asyncio
import json
from typing import Any

import anthropic
import httpx2
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from faro.agent.models import (
    MODEL,
    PROVIDER,
    Declared,
    ModelFailedError,
    Models,
    failure,
)
from faro.agent.retries import Retries, Wait, retry_after
from faro.agent.texts import FIXED
from faro.contracts import validator

from .conftest import Harness, denied, tool_error
from .test_block import Chat, control_shown, reply
from .test_offer import answer_offer, filed, offer_of

STATUS = "¿Cómo está mi débito 1177?"


def fixed(jitter: float = 0.5) -> Retries:
    async def slept(_: float) -> None:
        pass

    return Retries(sleep=slept, jitter=lambda low, high: low + (high - low) * jitter)


# The policy


def test_three_attempts_at_most() -> None:
    policy = fixed()

    assert policy.after(1, 0.0) is not None
    assert policy.after(2, 0.0) is not None
    assert policy.after(3, 0.0) is None


def test_a_backoff_is_jittered_up_to_one_then_two_seconds() -> None:
    low, high = fixed(0.0), fixed(1.0)

    assert low.after(1, 0.0) == Wait(0.0, "backoff")
    assert high.after(1, 0.0) == Wait(1.0, "backoff")
    assert high.after(2, 0.0) == Wait(2.0, "backoff")


def test_the_providers_retry_after_is_waited_up_to_ten_seconds() -> None:
    policy = fixed()

    assert policy.after(1, 0.0, retry_after_s=4.0) == Wait(4.0, "retry_after")
    assert policy.after(1, 0.0, retry_after_s=10.0) == Wait(10.0, "retry_after")
    assert policy.after(1, 0.0, retry_after_s=10.5) is None


def test_output_that_didnt_parse_is_tried_again_at_once() -> None:
    assert fixed().after(1, 0.0, at_once=True) == Wait(0.0, "none")


def test_no_wait_crosses_the_turns_deadline() -> None:
    policy = fixed(1.0)

    assert policy.after(1, 58.0) == Wait(1.0, "backoff")
    assert policy.after(1, 59.0) is None
    assert policy.after(1, 52.0, retry_after_s=8.0) is None
    assert policy.after(1, 60.0, at_once=True) is None


def test_a_wait_is_recorded_in_milliseconds_with_what_set_it() -> None:
    assert Wait(1.2346, "backoff").fields() == {
        "wait_ms": 1235,
        "waited_for": "backoff",
    }


@pytest.mark.parametrize(
    ("headers", "seconds"),
    [
        ({"retry-after": "3"}, 3.0),
        ({"retry-after-ms": "1500", "retry-after": "2"}, 1.5),
        ({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, None),
        ({}, None),
        (None, None),
    ],
)
def test_retry_after_is_read_in_seconds(
    headers: dict[str, str] | None, seconds: float | None
) -> None:
    assert retry_after(headers) == seconds


# Model calls


def status_error(kind: Any, status: int, **headers: str) -> Exception:
    request = httpx2.Request("POST", "https://api.anthropic.example")
    response = httpx2.Response(status, request=request, headers=headers)
    error: Exception = kind("failed", response=response, body=None)
    return error


def answered() -> AIMessage:
    return AIMessage(content="Hola.", response_metadata={"model_name": MODEL})


def scripted(
    responses: list[Any], policy: Retries, elapsed: float = 0.0
) -> tuple[Models, list[dict[str, Any]], list[float]]:
    entries: list[dict[str, Any]] = []
    waits: list[float] = []
    pending = list(responses)

    async def record(kind: str, **fields: Any) -> None:
        entries.append({"kind": kind, **fields})

    async def invoke(_: Any) -> Any:
        answer = pending.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def slept(seconds: float) -> None:
        waits.append(seconds)

    timed = Retries(sleep=slept, jitter=policy.jitter)
    declared = Declared(lambda _: RunnableLambda(invoke), PROVIDER, MODEL)
    made = Models(declared, record, timed, lambda: elapsed)
    return made, entries, waits


def entry_fits(fields: dict[str, Any]) -> bool:
    common = {
        "sign_in": "5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68",
        "entry_key": "2026-09-29T18:00:00.000Z#0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50#0001",
        "turn_id": "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50",
        "seq": 1,
        "at": "2026-09-29T18:00:00.000Z",
        "source": "demo",
        "expires_at": 1,
    }
    return validator("execution-record").is_valid({**common, **fields})


def replied(made: Models) -> str:
    return asyncio.run(made.reply("Hola", "", "Spanish"))


def test_a_rate_limited_call_waits_the_providers_retry_after_and_is_answered() -> None:
    limited = status_error(anthropic.RateLimitError, 429, **{"retry-after": "2"})
    made, entries, waits = scripted([limited, answered()], fixed())

    assert replied(made) == "Hola."

    assert waits == [2.0]
    assert [(e["attempt"], e["outcome"]) for e in entries] == [
        (1, "rate_limited"),
        (2, "ok"),
    ]
    assert "wait_ms" not in entries[0]
    assert (entries[1]["wait_ms"], entries[1]["waited_for"]) == (2000, "retry_after")
    assert all(entry_fits(e) for e in entries)


@pytest.mark.parametrize(
    "error",
    [
        status_error(anthropic.InternalServerError, 500),
        status_error(anthropic.APIStatusError, 529),
        anthropic.APITimeoutError(httpx2.Request("POST", "https://a.example")),
        anthropic.APIConnectionError(
            request=httpx2.Request("POST", "https://a.example")
        ),
    ],
)
def test_a_server_error_a_timeout_or_no_connection_is_tried_three_times(
    error: Exception,
) -> None:
    made, entries, waits = scripted([error] * 3, fixed(1.0))

    with pytest.raises(ModelFailedError):
        replied(made)

    assert [e["attempt"] for e in entries] == [1, 2, 3]
    assert {e["outcome"] for e in entries} == {failure(error)}
    assert waits == [1.0, 2.0]
    assert [e.get("waited_for") for e in entries] == [None, "backoff", "backoff"]
    assert all(entry_fits(e) for e in entries)


def test_no_connection_is_named_apart_from_a_timeout() -> None:
    request = httpx2.Request("POST", "https://a.example")

    assert failure(anthropic.APIConnectionError(request=request)) == "connection_error"
    assert failure(anthropic.APITimeoutError(request)) == "timeout"


def test_output_that_didnt_parse_is_tried_again_without_waiting() -> None:
    empty = AIMessage(content="", response_metadata={"model_name": MODEL})
    made, entries, waits = scripted([empty, answered()], fixed())

    assert replied(made) == "Hola."
    assert waits == [0.0]
    assert entries[1]["waited_for"] == "none"


@pytest.mark.parametrize("status", [400, 401, 403])
def test_a_request_the_provider_refuses_isnt_repeated(status: int) -> None:
    refused = status_error(anthropic.APIStatusError, status)
    made, entries, waits = scripted([refused], fixed())

    with pytest.raises(ModelFailedError):
        replied(made)
    assert [(e["attempt"], e["outcome"]) for e in entries] == [(1, "error")]
    assert waits == []


def test_a_retry_after_longer_than_ten_seconds_ends_the_call_at_once() -> None:
    limited = status_error(anthropic.RateLimitError, 429, **{"retry-after": "30"})
    made, entries, waits = scripted([limited], fixed())

    with pytest.raises(ModelFailedError):
        replied(made)
    assert len(entries) == 1 and waits == []


def test_a_turn_past_its_deadline_tries_no_more() -> None:
    made, entries, _ = scripted(
        [status_error(anthropic.InternalServerError, 503)], fixed(), elapsed=60.0
    )

    with pytest.raises(ModelFailedError):
        replied(made)
    assert len(entries) == 1


def test_a_router_that_keeps_failing_ends_in_the_fixed_reply(harness: Harness) -> None:
    harness.script.route_error = status_error(anthropic.InternalServerError, 503)
    chat = Chat(harness)

    events = chat.say(STATUS, requests=["card_status"])

    routes = [e for e in chat.entries() if e["kind"] == "model_call"]
    assert [e["attempt"] for e in routes] == [1, 2, 3]
    assert FIXED["unavailable"]["es"] in reply(events)


# Tool calls


def failing(harness: Harness, tool: str, times: int, answer: Any = tool_error) -> None:
    """
    The Gateway fails the tool's first attempts, then answers as the bank does.
    """
    bank, left = harness.bank.answer, [times]

    def gateway(request: Any) -> Any:
        name = json.loads(request.content)["params"]["name"].rpartition("___")[2]
        if name == tool and left[0] > 0:
            left[0] -= 1
            return answer()
        return bank(request)

    harness.script.gateway = gateway


def attempts(chat: Chat, tool: str) -> list[dict[str, Any]]:
    return [e for e in chat.entries() if e["kind"] == "tool_call" and e["tool"] == tool]


@pytest.mark.parametrize("times", [1, 2])
def test_a_read_that_fails_within_its_retries_is_answered(
    harness: Harness, times: int
) -> None:
    failing(harness, "get_card", times)
    harness.script.replies = ["{card}: {card.status}, {card.expiration}."]
    chat = Chat(harness)

    chat.say(STATUS, requests=["card_status"], last_four="1177")

    made = attempts(chat, "get_card")
    assert [(e["attempt"], e["outcome"]) for e in made] == [
        *((n + 1, "failed") for n in range(times)),
        (times + 1, "ok"),
    ]
    assert len({e["call_id"] for e in made}) == 1
    assert all(e["waited_for"] == "backoff" for e in made[1:])
    assert len(harness.waits) == times
    assert chat.decision()["outcome_class"] == "answer"


def test_a_read_that_keeps_failing_offers_tool_failure_and_cites_its_last_attempt(
    harness: Harness,
) -> None:
    failing(harness, "get_card", 3)
    chat = Chat(harness)

    failed = chat.say(STATUS, requests=["card_status"], last_four="1177")
    made = attempts(chat, "get_card")
    answer_offer(chat, "accept", failed)

    assert [e["attempt"] for e in made] == [1, 2, 3]
    assert offer_of(failed)["reason_code"] == "tool_failure"
    assert chat.decision()["outcome_class"] in ("abstain", "hand_off")
    case = filed(harness)
    (cited,) = [e for e in case["payload"]["evidence"] if e["tool"] == "get_card"]
    assert (cited["call_id"], cited["called_at"], cited["outcome"]) == (
        made[-1]["call_id"],
        made[-1]["called_at"],
        "error",
    )
    # file_handoff checked the payload against the record before filing it.
    assert (case["flagged"], case["validation_errors"]) == (False, [])


def test_a_denial_is_never_tried_again(harness: Harness) -> None:
    failing(harness, "get_card", 3, denied)
    chat = Chat(harness)

    chat.say(STATUS, requests=["card_status"], last_four="1177")

    assert [e["outcome"] for e in attempts(chat, "get_card")] == ["denied"]
    assert harness.waits == []


def test_a_block_whose_answer_was_lost_is_tried_again_under_its_confirmation(
    harness: Harness,
) -> None:
    chat = Chat(harness)
    shown = control_shown(chat)
    failing(harness, "block_card", 1)

    done = chat.press("confirm", shown)

    made = attempts(chat, "block_card")
    assert [(e["attempt"], e["outcome"]) for e in made] == [(1, "failed"), (2, "ok")]
    assert made[1]["result"]["block_outcome"] == "verified"
    assert FIXED["block_verified"]["es"].split("{")[0] in reply(done)
