"""
A runtime session refused at its sign-in's limit is ended through StopRuntimeSession with the caller's own token, at
the URL the runtime's ARN names, after the refusal has answered, and tried again while AgentCore answers that one of
the session's requests runs (ADR-0004, Ending a runtime session; OPS-08).
"""

import asyncio
from typing import Any

import httpx
import pytest

from faro.agent.sessions import SESSION_HEADER, RuntimeEnding

ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/banking_agent_local_agent-AbCdEfGhIj"
STOP_URL = (
    "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
    "arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2Fbanking_agent_local_agent-AbCdEfGhIj"
    "/stopruntimesession?qualifier=DEFAULT"
)


class Parameters:
    def __init__(self) -> None:
        self.reads: list[str] = []

    def get_parameter(self, Name: str) -> dict[str, Any]:  # noqa: N803
        self.reads.append(Name)
        return {"Parameter": {"Value": ARN}}


class Answers:
    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.statuses.pop(0))


def ending(
    answers: Answers, parameters: Parameters, waits: list[float]
) -> RuntimeEnding:
    async def wait(seconds: float) -> None:
        waits.append(seconds)

    return RuntimeEnding(
        parameters,
        "/faro-local/runtime/arn",
        "us-east-1",
        httpx.AsyncClient(transport=httpx.MockTransport(answers)),
        sleep=wait,
    )


def test_a_session_is_ended_with_the_callers_token_after_a_wait() -> None:
    answers, parameters = Answers(200, 200), Parameters()
    waits: list[float] = []
    runtime = ending(answers, parameters, waits)

    async def twice() -> list[str | None]:
        return [
            await runtime.end("token-1", "session-1"),
            await runtime.end("token-2", "session-2"),
        ]

    assert asyncio.run(twice()) == ["stopped", "stopped"]
    assert parameters.reads == ["/faro-local/runtime/arn"]
    first = answers.requests[0]
    assert (str(first.url), first.method) == (STOP_URL, "POST")
    assert first.headers["Authorization"] == "Bearer token-1"
    assert first.headers[SESSION_HEADER] == "session-1"
    assert waits == [0.5, 0.5]


def test_a_conflict_is_tried_again_and_an_ended_session_is_not_found() -> None:
    answers = Answers(409, 409, 404)
    waits: list[float] = []

    assert (
        asyncio.run(ending(answers, Parameters(), waits).end("t", "s")) == "not_found"
    )
    assert waits == [0.5, 1.0, 2.0]


@pytest.mark.parametrize("statuses", [(403,), (409, 409, 409, 409)])
def test_a_session_that_cant_be_ended_is_logged_and_nothing_raised(
    statuses: tuple[int, ...], caplog: pytest.LogCaptureFixture
) -> None:
    answers = Answers(*statuses)

    assert asyncio.run(ending(answers, Parameters(), []).end("t", "s")) is None
    assert len(answers.requests) == len(statuses)
    assert "couldn't end a runtime session" in caplog.text
    assert "Bearer" not in caplog.text


def test_ending_later_runs_on_the_loop_and_lets_go_of_its_task() -> None:
    answers = Answers(200)
    runtime = ending(answers, Parameters(), [])

    async def later() -> None:
        runtime.end_later("t", "s")
        assert len(runtime.tasks) == 1
        await asyncio.gather(*runtime.tasks)

    asyncio.run(later())
    assert len(answers.requests) == 1
    assert runtime.tasks == set()
