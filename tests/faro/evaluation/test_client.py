"""
The harness's AG-UI client: requests sent as the chat sends them, events kept with their arrival times, a sign-in kept
within decision 21's rate and sent again after rate_limited, a request the Runtime didn't answer as the contract says
reported as the harness's error, and the runtime session ended through the bearer token.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from faro.evaluation.client import (
    RESENDS,
    SESSION_HEADER,
    Client,
    HarnessError,
    Pace,
    Session,
    events,
)

INVOKE = "https://runtime.invalid/runtimes/agent/invocations?qualifier=DEFAULT"
STOP = "https://runtime.invalid/runtimes/agent/stopruntimesession?qualifier=DEFAULT"
FINISHED = [
    {"type": "RUN_STARTED", "threadId": "t", "runId": "r"},
    {"type": "RUN_FINISHED", "threadId": "t", "runId": "r"},
]
RATE_LIMITED = [{"type": "RUN_ERROR", "message": "Slow down.", "code": "rate_limited"}]


def sse(sent: list[dict[str, Any]]) -> bytes:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in sent).encode()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def client(
    handler: Callable[[httpx.Request], httpx.Response], slept: list[float] | None = None
) -> Client:
    sleeps = slept if slept is not None else []
    return Client(
        INVOKE,
        STOP,
        httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )


def session(clock: Clock | None = None) -> Session:
    clock = clock or Clock()
    return Session(pace=Pace(clock=clock, sleep=clock.sleep))


def test_events_are_parsed_as_they_arrive_with_a_time_each() -> None:
    lines = ['data: {"type": "RUN_STARTED",', 'data:  "runId": "r"}', "", ": a comment"]
    lines += ["", 'data:{"type": "RUN_FINISHED"}']

    parsed = list(events(iter(lines), 0.0))

    assert [p["event"] for p in parsed] == [
        {"type": "RUN_STARTED", "runId": "r"},
        {"type": "RUN_FINISHED"},
    ]
    assert all(isinstance(p["at_ms"], int) for p in parsed)


def test_a_message_is_sent_as_the_chat_sends_it() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=sse(FINISHED))

    case = session()
    played = client(handler).turn("token", case, text="¿Mi tarjeta está activa?")

    [request] = seen
    body = json.loads(request.content)
    assert request.headers["Authorization"] == "Bearer token"
    assert request.headers[SESSION_HEADER] == case.session_id
    assert len(case.session_id) >= 33
    assert request.headers["Accept"] == "text/event-stream"
    assert body["threadId"] == case.thread_id and body["runId"] == played.run_id
    assert [m["content"] for m in body["messages"]] == ["¿Mi tarjeta está activa?"]
    assert [e["event"]["type"] for e in played.events] == [
        "RUN_STARTED",
        "RUN_FINISHED",
    ]
    assert played.resent == 0


def test_a_control_is_sent_as_a_resume_and_the_session_opens_with_a_warmup() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, content=sse(FINISHED))

    resume = {"interruptId": "i", "status": "resolved", "payload": {"kind": "confirm"}}
    harness, case = client(handler), session()
    harness.warmup("token", case)
    harness.turn("token", case, resume=resume)

    assert bodies[0]["forwardedProps"] == {"warmup": True}
    assert bodies[0]["messages"] == []
    assert bodies[1]["resume"] == [resume] and bodies[1]["messages"] == []
    assert bodies[0]["runId"] != bodies[1]["runId"]


def test_a_sign_in_waits_out_its_window_before_the_eleventh_request() -> None:
    clock = Clock()
    pace = Pace(rate=10, window=60, clock=clock, sleep=clock.sleep)

    for _ in range(10):
        pace.wait()
        clock.now += 1
    pace.wait()

    assert clock.slept == [50]
    pace.wait()
    assert clock.slept == [50, 1]


def test_a_rate_limited_request_is_sent_again_after_a_whole_window() -> None:
    answers = [RATE_LIMITED, FINISHED]
    run_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        run_ids.append(json.loads(request.content)["runId"])
        return httpx.Response(200, content=sse(answers.pop(0)))

    clock = Clock()
    played = client(handler).turn("token", session(clock), text="hola")

    assert played.resent == 1 and played.run_id == run_ids[-1]
    assert played.events[-1]["event"]["type"] == "RUN_FINISHED"
    assert clock.slept == [60]


def test_a_request_still_rate_limited_is_given_up_with_its_refusal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse(RATE_LIMITED))

    played = client(handler).turn("token", session(), text="hola")

    assert played.resent == RESENDS
    assert played.events[-1]["event"]["code"] == "rate_limited"


@pytest.mark.parametrize(
    ("answer", "refusal"),
    [
        (httpx.Response(401), "http 401"),
        (httpx.Response(200, content=sse(FINISHED[:1])), "ended before its run"),
        (httpx.Response(200, content=b""), "ended before its run"),
    ],
)
def test_an_answer_outside_the_contract_is_the_harnesss_error(
    answer: httpx.Response, refusal: str
) -> None:
    with pytest.raises(HarnessError, match=refusal):
        client(lambda request: answer).turn("token", session(), text="hola")


def test_a_timeout_is_the_harnesss_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(HarnessError, match="timed out"):
        client(handler).turn("token", session(), text="hola")


def test_the_session_ends_through_the_bearer_token_and_a_conflict_is_retried() -> None:
    answers = [409, 409, 200]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(answers.pop(0))

    slept: list[float] = []
    case = session()

    assert client(handler, slept).stop("token", case) == "stopped"
    assert str(seen[0].url) == STOP
    assert seen[0].headers["Authorization"] == "Bearer token"
    assert seen[0].headers[SESSION_HEADER] == case.session_id
    assert slept == [0.5, 1.0]


def test_a_session_already_ended_is_not_found_and_a_refusal_is_an_error() -> None:
    assert client(lambda r: httpx.Response(404)).stop("token", session()) == (
        "not_found"
    )
    with pytest.raises(HarnessError, match="http 403"):
        client(lambda r: httpx.Response(403)).stop("token", session())
