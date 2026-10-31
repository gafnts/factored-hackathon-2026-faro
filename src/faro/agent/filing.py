"""
Files a handoff through file_handoff, which is off the Gateway: the Runtime invokes its Lambda with its own role, and
forwards the customer's access token in the event, beside the tool's input, for the Lambda to check (ADR-0004, Where the
tools run, and its amendments of 2026-09-30). Filing is idempotent by the handoff's ID, so a call that fails is tried
again under the same call ID, three attempts in all, with decision 18's waits (OPS-04). Each attempt is one tool_call entry, whose input is the
handoff's ID alone: the payload is in the case, and the token is never recorded.
"""

import asyncio
import dataclasses
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    ReadTimeoutError,
)

from faro.agent.gateway import ToolCall, failed
from faro.agent.records import wall_time
from faro.agent.retries import RETRIES, Retries
from faro.contracts import validator

TOOL = "file_handoff"
THROTTLED = ("TooManyRequestsException", "ThrottlingException")

Record = Callable[..., Awaitable[Any]]


class LambdaClient(Protocol):
    def invoke(self, **request: Any) -> Any: ...


def classify(response: Any) -> dict[str, Any]:
    if response.get("FunctionError"):
        return failed("lambda_error")
    try:
        output = json.loads(response["Payload"].read())
    except (KeyError, ValueError):
        return failed("lambda_error")
    if not validator("tools", f"{TOOL}_output").is_valid(output):
        return failed("lambda_error")
    return {"outcome": output["outcome"], "result": output}


class Filing:
    def __init__(
        self,
        function: str,
        client: LambdaClient,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.function = function
        self.client = client
        self.now = now

    def _invoke(self, event: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        try:
            response = self.client.invoke(
                FunctionName=self.function,
                InvocationType="RequestResponse",
                Payload=json.dumps(event).encode(),
            )
        except (ReadTimeoutError, ConnectTimeoutError):
            return None, failed("timeout")
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            return None, failed("throttled" if code in THROTTLED else "transport")
        except BotoCoreError:
            return None, failed("transport")
        request_id = response.get("ResponseMetadata", {}).get("RequestId")
        return request_id, classify(response)

    async def attempt(
        self, call_id: str, attempt: int, arguments: dict[str, Any], token: str
    ) -> ToolCall:
        called_at = wall_time(self.now())
        started = time.perf_counter()
        request_id, classified = await asyncio.to_thread(
            self._invoke, {"token": token, "input": arguments}
        )
        handoff_id = (arguments.get("draft") or arguments.get("payload") or {}).get(
            "handoff_id"
        )
        return ToolCall(
            call_id=call_id,
            tool=TOOL,
            called_at=called_at,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_id=request_id,
            input={"handoff_id": handoff_id},
            via="direct",
            attempt=attempt,
            **classified,
        )

    async def file(
        self,
        arguments: dict[str, Any],
        token: str,
        record: Record,
        retries: Retries = RETRIES,
        elapsed: Callable[[], float] = lambda: 0.0,
    ) -> ToolCall:
        """
        arguments carry their call_id, which the graph drew so the facts the tool adds can cite it; a failed attempt
        waits as decision 18 says before the next.
        """
        call_id = arguments["call_id"]
        attempt, wait = 1, None
        while True:
            call = await self.attempt(call_id, attempt, arguments, token)
            if wait is not None:
                call = dataclasses.replace(call, waited=wait.fields())
            wait = (
                retries.after(attempt, elapsed()) if call.outcome == "failed" else None
            )
            await record("tool_call", last=wait is None, **call.entry())
            if wait is None:
                return call
            await retries.sleep(wait.seconds)
            attempt += 1


def new_call_id() -> str:
    return str(uuid.uuid4())
