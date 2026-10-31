"""
Calls the Gateway's tools as the signed-in customer: a stateless MCP tools/call over HTTP with the customer's own access
token, which the Gateway validates and Cedar checks (ADR-0004, A turn, end to end). Each tool is named on its own
target, <target>___<tool>, since the reads and the block are separate Lambdas (Where the tools run). Each call becomes
one tool_call entry of the execution record. A tool's own output is checked against its contract here too, since the
record keeps it whole; a call the Lambda never answered is failed, and Cedar's JSON-RPC -32002 is denied (POL-49). A
fault plan's answer is failed too, with the error it plans, marked as planned (ADR-0004's amendment of 2026-10-01).
"""

import json
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from faro.agent.records import wall_time
from faro.contracts import validator

DENIED = -32002
TIMEOUT_S = 10.0
REQUEST_ID_HEADERS = ("x-amzn-requestid", "x-amzn-request-id")


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    tool: str
    called_at: str
    latency_ms: int
    request_id: str | None
    input: dict[str, Any]
    outcome: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    via: str = "gateway"
    attempt: int = 1
    waited: dict[str, Any] | None = None

    def entry(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "call_id": self.call_id,
            "tool": self.tool,
            "via": self.via,
            "attempt": self.attempt,
            **(self.waited or {}),
            "called_at": self.called_at,
            "latency_ms": self.latency_ms,
            "request_id": self.request_id,
            "input": self.input,
            "outcome": self.outcome,
        }
        if self.error is not None:
            fields["error"] = self.error
        else:
            fields["result"] = self.result
        return fields


def failed(code: str, jsonrpc_code: int | None = None) -> dict[str, Any]:
    return {"outcome": "failed", "error": {"code": code, "jsonrpc_code": jsonrpc_code}}


def planned(code: str) -> dict[str, Any]:
    """
    A fault plan's failure, which the tool answered: recorded as the failure it stands for, and marked as planned.
    """
    return {"code": code, "jsonrpc_code": None, "planned": True}


def json_rpc_body(response: httpx.Response) -> Any:
    text = response.text
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        text = next(
            (ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")), ""
        )
    return json.loads(text)


def classify(tool: str, response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 429:
        return failed("throttled")
    if response.status_code != 200:
        return failed("transport")
    try:
        body = json_rpc_body(response)
    except json.JSONDecodeError:
        return failed("transport")
    if not isinstance(body, dict):
        return failed("transport")
    if "error" in body:
        code = body["error"].get("code") if isinstance(body["error"], dict) else None
        code = code if isinstance(code, int) else None
        if code == DENIED:
            return {
                "outcome": "denied",
                "error": {"code": "denied", "jsonrpc_code": code},
            }
        return failed("transport", code)
    result = body.get("result")
    if not isinstance(result, dict) or result.get("isError"):
        return failed("lambda_error")
    try:
        output = json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return failed("lambda_error")
    if not validator("tools", f"{tool}_output").is_valid(output):
        return failed("lambda_error")
    if output["outcome"] == "fault":
        return {"outcome": "failed", "error": planned(output["error"])}
    return {"outcome": output["outcome"], "result": output}


class Gateway:
    def __init__(
        self,
        url: str,
        targets: Mapping[str, str],
        client: httpx.AsyncClient | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.url = url
        self.targets = targets
        self.client = client
        self.now = now

    async def call(
        self,
        tool: str,
        arguments: dict[str, Any],
        token: str,
        call_id: str | None = None,
        attempt: int = 1,
    ) -> ToolCall:
        """
        A call's attempts share its call_id.
        """
        call_id = call_id or str(uuid.uuid4())
        called_at = wall_time(self.now())
        body = {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": f"{self.targets[tool]}___{tool}",
                "arguments": arguments,
            },
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        }
        started = time.perf_counter()
        request_id = None
        try:
            if self.client is None:
                async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                    response = await client.post(self.url, json=body, headers=headers)
            else:
                response = await self.client.post(self.url, json=body, headers=headers)
            request_id = next(
                (
                    response.headers[h]
                    for h in REQUEST_ID_HEADERS
                    if h in response.headers
                ),
                None,
            )
            classified = classify(tool, response)
        except httpx.TimeoutException:
            classified = failed("timeout")
        except httpx.HTTPError:
            classified = failed("transport")
        return ToolCall(
            call_id=call_id,
            tool=tool,
            called_at=called_at,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_id=request_id,
            input=arguments,
            attempt=attempt,
            **classified,
        )
