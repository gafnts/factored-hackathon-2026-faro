"""
The harness's AG-UI client (ADR-0005, Running a case): a request sent to the deployed Runtime as the chat sends it, with
the case's bearer token and runtime session, and each event kept with its arrival time from the send, which M-05 reads
(ADR-0005, Reporting). A sign-in stays within decision 21's rate, whose window the client waits out before sending, so
no wait counts as latency; a request the Runtime still refuses as rate_limited is sent again once the window allows.
A case's runtime session opens with the chat's warmup (decision 20) and ends with StopRuntimeSession through the case's
bearer token, the path AgentCore documents for a runtime behind a JWT authorizer.
"""

import itertools
import json
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

RATE, WINDOW = 10, 60.0
RESENDS = 3
STOP_TRIES = 5
TIMEOUT = httpx.Timeout(10.0, read=120.0)
SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
ENDED = ("RUN_FINISHED", "RUN_ERROR")


class HarnessError(RuntimeError):
    """
    A request the Runtime didn't answer as the chat's contract says: another HTTP status, a stream that ended before
    its run did, or a timeout. The case is the harness's error, never graded.
    """


class Pace:
    """
    A sign-in's requests within decision 21's rate: at most `rate` in any `window` seconds.
    """

    def __init__(
        self,
        rate: int = RATE,
        window: float = WINDOW,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate, self.window = rate, window
        self.clock, self.sleep = clock, sleep
        self.sent: deque[float] = deque()

    def wait(self) -> None:
        self._drop()
        if len(self.sent) >= self.rate:
            self.sleep(self.sent[0] + self.window - self.clock())
            self._drop()
        self.sent.append(self.clock())

    def refused(self) -> None:
        """
        The Runtime counted more than this window holds, so the next request waits a whole window.
        """
        self.sleep(self.window)
        self.sent.clear()

    def _drop(self) -> None:
        while self.sent and self.clock() - self.sent[0] >= self.window:
            self.sent.popleft()


@dataclass
class Played:
    run_id: str
    events: list[dict[str, Any]]
    resent: int = 0


@dataclass
class Session:
    """
    A case's runtime session and thread, and its sign-in's pace.
    """

    session_id: str = field(default_factory=lambda: f"eval-session-{uuid.uuid4().hex}")
    thread_id: str = field(default_factory=lambda: f"eval-thread-{uuid.uuid4().hex}")
    pace: Pace = field(default_factory=Pace)


def events(lines: Iterable[str], started: float) -> Iterator[dict[str, Any]]:
    """
    Server-sent events, each kept with its arrival time: when the blank line that ends it arrives.
    """
    data: list[str] = []
    for line in itertools.chain(lines, [""]):
        if line.startswith("data:"):
            data.append(line.removeprefix("data:").removeprefix(" "))
        elif not line and data:
            yield {
                "at_ms": round((time.perf_counter() - started) * 1000),
                "event": json.loads("\n".join(data)),
            }
            data = []


def refused(played: list[dict[str, Any]], code: str) -> bool:
    return bool(played) and played[-1]["event"].get("code") == code


class Client:
    def __init__(
        self,
        invoke_url: str,
        stop_url: str,
        http: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.invoke_url = invoke_url
        self.stop_url = stop_url
        self.http = http
        self.sleep = sleep

    def warmup(self, token: str, session: Session) -> Played:
        return self.send(token, session, {"forwardedProps": {"warmup": True}})

    def turn(
        self,
        token: str,
        session: Session,
        text: str | None = None,
        resume: Mapping[str, Any] | None = None,
    ) -> Played:
        if resume is not None:
            return self.send(token, session, {"resume": [dict(resume)]})
        message = {"id": uuid.uuid4().hex, "role": "user", "content": text}
        return self.send(token, session, {"messages": [message]})

    def send(self, token: str, session: Session, given: Mapping[str, Any]) -> Played:
        resent = 0
        while True:
            session.pace.wait()
            run_id = f"run-{uuid.uuid4().hex[:12]}"
            body = {
                "threadId": session.thread_id,
                "runId": run_id,
                "messages": [],
                **given,
            }
            played = self._stream(token, session.session_id, body)
            if not refused(played, "rate_limited") or resent == RESENDS:
                return Played(run_id, played, resent)
            resent += 1
            session.pace.refused()

    def expired(self, token: str, session: Session, text: str) -> int:
        """
        A message sent with a token past its end (EVL-03): the status the Runtime's authorizer answers, kept whatever
        it is, since a 200 is what the case catches.
        """
        body = {
            "threadId": session.thread_id,
            "runId": f"run-{uuid.uuid4().hex[:12]}",
            "messages": [{"id": uuid.uuid4().hex, "role": "user", "content": text}],
        }
        headers = {
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
            SESSION_HEADER: session.session_id,
        }
        try:
            response = self.http.post(
                self.invoke_url, json=body, headers=headers, timeout=TIMEOUT
            )
        except httpx.HTTPError as error:
            raise HarnessError(f"the request failed: {type(error).__name__}") from error
        return response.status_code

    def stop(self, token: str, session: Session) -> str:
        """
        Ends the case's runtime session: `stopped`, or `not_found` when it had already ended.
        """
        for attempt in range(STOP_TRIES):
            response = self.http.post(
                self.stop_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    SESSION_HEADER: session.session_id,
                },
                timeout=TIMEOUT,
            )
            if response.status_code == 200:
                return "stopped"
            if response.status_code == 404:
                return "not_found"
            if response.status_code != 409:
                break
            self.sleep(0.5 * 2**attempt)
        raise HarnessError(f"StopRuntimeSession answered http {response.status_code}")

    def _stream(
        self, token: str, session_id: str, body: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        headers = {
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
            SESSION_HEADER: session_id,
        }
        started = time.perf_counter()
        try:
            with self.http.stream(
                "POST", self.invoke_url, json=body, headers=headers, timeout=TIMEOUT
            ) as response:
                if response.status_code != 200:
                    raise HarnessError(
                        f"the Runtime answered http {response.status_code}"
                    )
                played = list(events(response.iter_lines(), started))
        except httpx.TimeoutException as error:
            raise HarnessError("the Runtime timed out") from error
        except httpx.HTTPError as error:
            raise HarnessError(f"the request failed: {type(error).__name__}") from error
        if not played or played[-1]["event"].get("type") not in ENDED:
            raise HarnessError("the stream ended before its run did")
        return played


class Gateway:
    """
    Direct MCP calls to the Gateway with a case's bearer token (ADR-0005's amendment of 2026-10-01): what a
    compromised agent or a customer who skips the agent could send. Each response is evidence, however the call went,
    so a failure here never raises.
    """

    def __init__(
        self, url: str, targets: Mapping[str, str], http: httpx.Client
    ) -> None:
        self.url = url
        self.targets = dict(targets)
        self.http = http

    def tools_list(self, token: str) -> dict[str, Any]:
        return self._post(token, "tools/list", {})

    def call(
        self,
        token: str,
        tool: str,
        arguments: Mapping[str, Any],
        name: str | None = None,
    ) -> dict[str, Any]:
        name = name if name is not None else f"{self.targets[tool]}___{tool}"
        return self._post(
            token, "tools/call", {"name": name, "arguments": dict(arguments)}
        )

    def _post(self, token: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        }
        try:
            response = self.http.post(self.url, json=body, headers=headers)
        except httpx.HTTPError as error:
            return {"status": None, "body": f"unreachable: {type(error).__name__}"}
        text = response.text
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            text = next(
                (
                    line[5:].strip()
                    for line in text.splitlines()
                    if line.startswith("data:")
                ),
                "",
            )
        try:
            return {"status": response.status_code, "body": json.loads(text)}
        except json.JSONDecodeError:
            return {"status": response.status_code, "body": text}
