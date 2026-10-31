"""
The agent calls the Gateway's tools with the customer's own token, and classifies every answer as the execution record
does: the tool's own outcomes kept whole, a Lambda that failed or broke its contract as failed, and Cedar's JSON-RPC
-32002 as denied, never failed (POL-49; OPS-06; AI-04).
"""

import asyncio
import json
from importlib.resources import files
from typing import Any

import httpx
import pytest

from faro.agent.gateway import Gateway, ToolCall

URL = "https://gateway.example/mcp"
TARGETS = {"list_cards": "reads", "get_card": "reads", "block_card": "block"}
ARGUMENTS = {
    "customer_id": "CLI-EXAMPLE00001",
    "origin_jti": "5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68",
}


def list_cards_outputs() -> list[dict[str, Any]]:
    text = (
        files("faro.contracts")
        .joinpath("examples/tools.list_cards_output.json")
        .read_text(encoding="utf-8")
    )
    loaded: list[dict[str, Any]] = json.loads(text)
    return loaded


def ok_output() -> dict[str, Any]:
    return next(o for o in list_cards_outputs() if o["outcome"] == "ok")


def result_body(output: Any) -> dict[str, Any]:
    text = output if isinstance(output, str) else json.dumps(output)
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": text}], "isError": False},
    }


def call(
    respond: Any, seen: list[httpx.Request] | None = None, tool: str = "list_cards"
) -> ToolCall:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if isinstance(respond, Exception):
            raise respond
        response: httpx.Response = respond(request)
        return response

    async def go() -> ToolCall:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await Gateway(URL, TARGETS, client).call(
                tool, ARGUMENTS, "customer-token"
            )

    return asyncio.run(go())


def test_the_call_carries_the_customers_token_and_the_tools_full_name() -> None:
    seen: list[httpx.Request] = []

    made = call(lambda _: httpx.Response(200, json=result_body(ok_output())), seen)

    assert seen[0].headers["authorization"] == "Bearer customer-token"
    body = json.loads(seen[0].content)
    assert body["method"] == "tools/call"
    assert body["params"] == {"name": "reads___list_cards", "arguments": ARGUMENTS}
    assert made.input == ARGUMENTS


@pytest.mark.parametrize("stream", [False, True])
def test_a_tools_output_is_kept_whole(stream: bool) -> None:
    output = ok_output()

    def respond(_: httpx.Request) -> httpx.Response:
        if stream:
            text = f"event: message\ndata: {json.dumps(result_body(output))}\n\n"
            return httpx.Response(
                200,
                text=text,
                headers={
                    "content-type": "text/event-stream",
                    "x-amzn-requestid": "gw-1",
                },
            )
        return httpx.Response(200, json=result_body(output))

    made = call(respond)

    assert (made.outcome, made.result, made.error) == ("ok", output, None)
    assert made.request_id == ("gw-1" if stream else None)
    assert made.entry()["result"] == output
    assert "error" not in made.entry()


def test_invalid_input_is_the_tools_own_outcome() -> None:
    refused = {
        "outcome": "invalid_input",
        "errors": [{"path": "/origin_jti", "rule": "format"}],
    }

    made = call(lambda _: httpx.Response(200, json=result_body(refused)))

    assert (made.outcome, made.result) == ("invalid_input", refused)


@pytest.mark.parametrize(
    "body",
    [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "An error occurred"}],
                "isError": True,
            },
        },
        result_body("not json"),
        result_body({"outcome": "ok", "is_fraud": True}),
        {"jsonrpc": "2.0", "id": 1, "result": {"content": []}},
    ],
)
def test_a_lambda_that_failed_or_broke_its_contract_is_failed(
    body: dict[str, Any],
) -> None:
    made = call(lambda _: httpx.Response(200, json=body))

    assert made.outcome == "failed"
    assert made.error == {"code": "lambda_error", "jsonrpc_code": None}
    assert made.result is None
    assert "result" not in made.entry()


@pytest.mark.parametrize("error", ["timeout", "throttled", "lambda_error"])
def test_a_planned_fault_is_failed_with_its_error_and_marked_planned(
    error: str,
) -> None:
    made = call(
        lambda _: httpx.Response(
            200, json=result_body({"outcome": "fault", "error": error})
        )
    )

    assert made.outcome == "failed"
    assert made.error == {"code": error, "jsonrpc_code": None, "planned": True}
    assert "result" not in made.entry()


def test_cedars_denial_is_denied_never_failed() -> None:
    body = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32002, "message": "Denied"}}

    made = call(lambda _: httpx.Response(200, json=body))

    assert made.outcome == "denied"
    assert made.error == {"code": "denied", "jsonrpc_code": -32002}


@pytest.mark.parametrize(
    ("respond", "error"),
    [
        (
            lambda _: httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32600}}
            ),
            {"code": "transport", "jsonrpc_code": -32600},
        ),
        (lambda _: httpx.Response(401), {"code": "transport", "jsonrpc_code": None}),
        (lambda _: httpx.Response(429), {"code": "throttled", "jsonrpc_code": None}),
        (lambda _: httpx.Response(502), {"code": "transport", "jsonrpc_code": None}),
        (
            lambda _: httpx.Response(200, text="<html>"),
            {"code": "transport", "jsonrpc_code": None},
        ),
        (
            lambda _: httpx.Response(200, json=[1]),
            {"code": "transport", "jsonrpc_code": None},
        ),
        (httpx.ReadTimeout("slow"), {"code": "timeout", "jsonrpc_code": None}),
        (httpx.ConnectError("down"), {"code": "transport", "jsonrpc_code": None}),
    ],
)
def test_a_call_the_lambda_never_answered_is_failed(
    respond: Any, error: dict[str, Any]
) -> None:
    made = call(respond)

    assert made.outcome == "failed"
    assert made.error == error


def test_the_block_is_called_on_its_own_target() -> None:
    seen: list[httpx.Request] = []

    call(lambda _: httpx.Response(200, json={"jsonrpc": "2.0"}), seen, "block_card")

    assert json.loads(seen[0].content)["params"]["name"] == "block___block_card"
