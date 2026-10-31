"""
Every attempt at a model call is recorded, on Claude Haiku 4.5, with the client's own retries and streaming off and
emit-messages and emit-tool-calls off, so nothing it writes reaches the chat unchecked; its usage and list-price cost
are recorded, and a failure is named by kind (ADR-0004, Models and What the chat receives; decision 18; OPS-02, M-05).
"""

import asyncio
import re
from typing import Any

import anthropic
import httpx2
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from faro.agent import models
from faro.agent.models import (
    MODEL,
    PROVIDER,
    Declared,
    ModelFailedError,
    Models,
    RequestDetails,
    RouterOutput,
    anthropic_factory,
    cost,
    failure,
    prompt_version,
    usage,
)
from faro.contracts import validator

USAGE = {
    "input_tokens": 1_000,
    "output_tokens": 100,
    "total_tokens": 1_100,
    "input_token_details": {"cache_read": 200, "cache_creation": 300},
}


def answer(**fields: Any) -> AIMessage:
    return AIMessage(response_metadata={"model_name": MODEL}, **fields)


def recorded(respond: Any) -> tuple[Models, list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []

    async def record(kind: str, **fields: Any) -> None:
        entries.append({"kind": kind, **fields})

    async def invoke(_: Any) -> Any:
        if isinstance(respond, Exception):
            raise respond
        return respond

    declared = Declared(lambda _: RunnableLambda(invoke), PROVIDER, MODEL)
    return Models(declared, record), entries


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


def test_the_calls_run_on_haiku_without_retries_streaming_or_emitted_events() -> None:
    make = anthropic_factory("model-key")
    reply, route, extract = make("reply"), make("route"), make("extract")

    assert (make.provider, make.model) == ("anthropic", MODEL)
    for runnable in (reply, route, extract):
        assert runnable.config["metadata"] == {  # type: ignore[attr-defined]
            "emit-messages": False,
            "emit-tool-calls": False,
        }
    chat: Any = reply.bound  # type: ignore[attr-defined]
    assert chat.model == MODEL
    assert (chat.max_retries, chat.disable_streaming) == (0, True)
    assert (chat.temperature, chat.max_tokens) == (0.0, 2048)


def test_a_call_names_the_provider_and_the_model_its_factory_declares() -> None:
    entries: list[dict[str, Any]] = []

    async def record(kind: str, **fields: Any) -> None:
        entries.append({"kind": kind, **fields})

    async def invoke(_: Any) -> AIMessage:
        return AIMessage("Hola.", response_metadata={"model_name": "baseline"})

    declared = Declared(lambda _: RunnableLambda(invoke), None, "baseline")
    made = Models(declared, record)

    asyncio.run(made.reply("Hola", "Request: card_status.", "Spanish"))

    (entry,) = entries
    assert entry_fits(entry)
    assert (entry["provider"], entry["model_requested"], entry["model_returned"]) == (
        None,
        "baseline",
        "baseline",
    )


def test_a_factory_that_declares_nothing_is_refused_before_any_call() -> None:
    async def record(kind: str, **fields: Any) -> None:
        raise AssertionError(kind)

    with pytest.raises(AttributeError, match="provider"):
        Models(lambda _: RunnableLambda(str), record)  # type: ignore[arg-type]


def test_a_route_call_records_its_output_usage_and_cost() -> None:
    parsed = RouterOutput(
        requests=["card_status"], has_request=True, complaint=False, language="es"
    )
    raw = answer(content="{}", usage_metadata=USAGE)
    made, entries = recorded({"raw": raw, "parsed": parsed, "parsing_error": None})

    assert asyncio.run(made.route("¿Mis tarjetas?")) == parsed

    (entry,) = entries
    assert entry_fits(entry)
    assert entry["output"] == {
        "requests": ["card_status"],
        "has_request": True,
        "complaint": False,
        "language": "es",
        "kind": "other",
    }
    assert entry["usage"] == {
        "input_tokens": 1_000,
        "output_tokens": 100,
        "cache_read_tokens": 200,
        "cache_write_tokens": 300,
    }
    assert (
        entry["cost_usd"] == (500 * 1.00 + 100 * 5.00 + 300 * 1.25 + 200 * 0.10) / 1e6
    )
    assert (entry["model_requested"], entry["model_returned"]) == (MODEL, MODEL)
    assert entry["prompt_version"] == prompt_version("route")


@pytest.mark.parametrize(
    ("respond", "outcome"),
    [
        (
            {
                "raw": answer(content="nope"),
                "parsed": None,
                "parsing_error": ValueError("x"),
            },
            "invalid_output",
        ),
        (RuntimeError("down"), "error"),
    ],
)
def test_a_route_call_that_fails_is_recorded_and_raised(
    respond: Any, outcome: str
) -> None:
    made, entries = recorded(respond)

    with pytest.raises(ModelFailedError):
        asyncio.run(made.route("hola"))

    assert entries[0]["outcome"] == outcome
    assert "output" not in entries[0]
    assert entry_fits(entries[0])


def test_a_reply_is_read_whole_and_an_empty_one_is_invalid() -> None:
    made, _ = recorded(answer(content="Estas son sus tarjetas.", usage_metadata=USAGE))
    assert (
        asyncio.run(made.reply("¿Mis tarjetas?", "{}", "Spanish"))
        == "Estas son sus tarjetas."
    )

    empty, entries = recorded(answer(content=""))
    with pytest.raises(ModelFailedError):
        asyncio.run(empty.reply("¿Mis tarjetas?", "{}", "Spanish"))
    assert entries[0]["outcome"] == "invalid_output"
    assert entries[0]["usage"]["input_tokens"] is None
    assert entries[0]["cost_usd"] is None


def status_error(kind: type[anthropic.APIStatusError], status: int) -> Exception:
    request = httpx2.Request("POST", "https://api.anthropic.example")
    return kind("failed", response=httpx2.Response(status, request=request), body=None)


def test_a_failure_is_named_by_kind() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.example")

    assert failure(status_error(anthropic.RateLimitError, 429)) == "rate_limited"
    assert failure(status_error(anthropic.InternalServerError, 529)) == "server_error"
    assert failure(status_error(anthropic.BadRequestError, 400)) == "error"
    assert failure(anthropic.APITimeoutError(request)) == "timeout"
    assert failure(RuntimeError("x")) == "error"


def test_usage_and_cost_are_unknown_without_usage() -> None:
    counted = usage(None)

    assert set(counted.values()) == {None}
    assert cost(counted) is None


def test_an_extraction_records_what_it_found_among_the_allowed_values() -> None:
    parsed = RequestDetails(
        card_type="credit",
        last_four="4821",
        block_reason="lost",
        cards=None,
        page=None,
        owner=None,
        conflict=None,
        service=None,
        question=None,
        language="es",
    )
    raw = answer(content="{}", usage_metadata=USAGE)
    made, entries = recorded({"raw": raw, "parsed": parsed, "parsing_error": None})

    found = asyncio.run(made.extract("Perdí la terminada en 4821.", "A block."))

    assert found == parsed
    (entry,) = entries
    assert entry_fits(entry)
    assert (entry["node"], entry["purpose"]) == ("resolve_card", "extract")
    assert entry["output"] == {
        "extracted": {
            "card_type": "credit",
            "last_four": "4821",
            "block_reason": "lost",
            "cards": None,
            "page": None,
            "owner": None,
            "conflict": None,
            "service": None,
            "question": None,
            "language": "es",
        }
    }
    assert entry["prompt_version"] == prompt_version("resolve_card")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("block_reason", "fraud"),
        ("block_reason", "customer_request"),
        ("service", "loan"),
        ("page", "previous"),
        ("language", "en"),
    ],
)
def test_an_extraction_offers_only_the_values_its_step_allows(
    field: str, value: str
) -> None:
    empty = {**dict.fromkeys(RequestDetails.model_fields), "language": "es"}
    with pytest.raises(ValueError):
        RequestDetails.model_validate({**empty, field: value})


def test_any_other_reason_is_recorded_as_the_model_named_it_and_mapped_for_the_graph() -> (
    None
):
    empty = {**dict.fromkeys(RequestDetails.model_fields), "language": "es"}
    parsed = RequestDetails.model_validate({**empty, "block_reason": "other_reason"})
    raw = answer(content="{}", usage_metadata=USAGE)
    made, entries = recorded({"raw": raw, "parsed": parsed, "parsing_error": None})

    found = asyncio.run(made.extract("Prefiero no decir por qué.", "A block."))

    (entry,) = entries
    assert entry["output"]["extracted"]["block_reason"] == "other_reason"
    assert found.details()["block_reason"] == "customer_request"
    lost = RequestDetails.model_validate({**empty, "block_reason": "lost"})
    assert lost.details()["block_reason"] == "lost"
    assert RequestDetails.model_validate(empty).details()["block_reason"] is None


def test_the_router_reads_what_the_last_reply_offered_after_its_prompt() -> None:
    parsed = RouterOutput(
        requests=["recent_transactions"],
        has_request=True,
        complaint=False,
        language="es",
    )
    sent: list[Any] = []

    async def invoke(messages: Any) -> Any:
        sent.append(messages)
        raw = answer(content="{}", usage_metadata=USAGE)
        return {"raw": raw, "parsed": parsed, "parsing_error": None}

    async def record(kind: str, **fields: Any) -> None:
        pass

    made = Models(Declared(lambda _: RunnableLambda(invoke), PROVIDER, MODEL), record)

    asyncio.run(made.route("¿Y los siguientes?", "A page was listed."))

    blocks = sent[0][0].content
    assert [block["text"] for block in blocks][1] == "A page was listed."


def test_every_prompt_has_a_version() -> None:
    for name in ("route", "resolve_card", "reply"):
        assert re.fullmatch("[0-9a-f]{16}", prompt_version(name))
    assert "{language}" in models.prompt("reply")
