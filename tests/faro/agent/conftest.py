"""
The entrypoint served as the Runtime serves it, through the SDK's app and the real wrapper, with every outside service
replaced: an in-memory checkpointer, bindings, record, and confirmations, a scripted model, a clock the tests move, a
Gateway answered by a mock transport, which by default serves the contract's example cards and blocks one under any
confirmation, and a Lambda client that runs file_handoff's own code over in-memory cases and the harness's record.
Tokens are unsigned, since the entrypoint doesn't check a signature the Runtime's authorizer already checked, and
file_handoff's check of them through Cognito is replaced by a check of their claims alone.
"""

import base64
import io
import json
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable, RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from starlette.testclient import TestClient

from faro.agent import app as entrypoint
from faro.agent.confirmations import MemoryConfirmations
from faro.agent.filing import Filing
from faro.agent.gateway import Gateway
from faro.agent.graph import build
from faro.agent.models import (
    MODEL,
    PROVIDER,
    Declared,
    Factory,
    HandoffText,
    RequestDetails,
    RouterOutput,
    TransactionChoice,
)
from faro.agent.retries import Retries
from faro.agent.texts import fill, render, values
from faro.tools import handoff
from faro.tools.cases import MemoryCases, MemoryFlags
from faro.tools.file_handoff import HandoffStores
from faro.tools.identity import Caller, caller_of, claims_of
from faro.tools.store import MemoryData

CLIENT_ID = "customer-client-0001"
SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
SETTINGS = entrypoint.Settings(
    client_id=CLIENT_ID,
    gateway_url="https://gateway.example/mcp",
    gateway_targets={
        "list_cards": "reads",
        "get_card": "reads",
        "get_available_credit": "reads",
        "find_transactions": "reads",
        "block_card": "block",
    },
    file_handoff_function="banking-agent-local-file-handoff",
    stamp={"snapshot": "b3b8b248f604ef9a", "pipeline_version": "5e1a9c3b7d2f4a68"},
    clock={"business_date": "2026-06-17", "as_of": "2026-06-18 06:00:00"},
    app_version="0123456789abcdef0123456789abcdef01234567",
)
EXTRACTED = {
    "card_type": None,
    "last_four": None,
    "block_reason": None,
    "cards": None,
    "page": None,
    "owner": None,
    "conflict": None,
    "service": None,
    "question": None,
}
USAGE = {
    "input_tokens": 120,
    "output_tokens": 30,
    "total_tokens": 150,
    "input_token_details": {"cache_read": 0, "cache_creation": 0},
}


def list_cards_output() -> dict[str, Any]:
    text = (
        files("faro.contracts")
        .joinpath("examples/tools.list_cards_output.json")
        .read_text(encoding="utf-8")
    )
    output: dict[str, Any] = next(o for o in json.loads(text) if o["outcome"] == "ok")
    return output


def find_transactions_output() -> dict[str, Any]:
    text = (
        files("faro.contracts")
        .joinpath("examples/tools.find_transactions_output.json")
        .read_text(encoding="utf-8")
    )
    output: dict[str, Any] = next(o for o in json.loads(text) if o["outcome"] == "ok")
    return output


def credit_outputs() -> dict[str, dict[str, Any]]:
    """
    The contract's examples of get_available_credit, by card.
    """
    text = (
        files("faro.contracts")
        .joinpath("examples/tools.get_available_credit_output.json")
        .read_text(encoding="utf-8")
    )
    return {o["card"]["card_id"]: o for o in json.loads(text) if o["outcome"] == "ok"}


def tools_data_items() -> list[dict[str, Any]]:
    text = (
        files("faro.contracts")
        .joinpath("examples/tools-data.json")
        .read_text(encoding="utf-8")
    )
    items: list[dict[str, Any]] = json.loads(text)
    return items


def tool_error() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "An error occurred"}],
                "isError": True,
            },
        },
    )


def denied() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32002, "message": "Denied"},
        },
    )


class Bank:
    """
    The example's cards, as the tools would answer about them in one sign-in: block_card blocks under any
    confirmation, and verified is what a test changes to see a block the read-back doesn't show.
    """

    def __init__(self) -> None:
        self.listed = list_cards_output()
        self.window = find_transactions_output()
        self.credit = credit_outputs()
        self.blocked: set[str] = set()
        self.verified = True

    def cards(self) -> list[dict[str, Any]]:
        return [
            {**c, "product_status": "Blocked"} if c["card_id"] in self.blocked else c
            for c in self.listed["cards"]
        ]

    def answer(self, request: httpx.Request) -> httpx.Response:
        params = json.loads(request.content)["params"]
        tool, arguments = params["name"].rpartition("___")[2], params["arguments"]
        stamped = {k: self.listed[k] for k in ("stamp", "clock")}
        if tool == "list_cards":
            return tool_result({**self.listed, "cards": self.cards()})
        if tool == "find_transactions":
            # The example window is the first card's; the others have none.
            own = arguments["card_id"] == self.window["card_id"]
            return tool_result(
                {
                    **self.window,
                    "card_id": arguments["card_id"],
                    "transactions": self.window["transactions"] if own else [],
                    "next_cursor": self.window["next_cursor"] if own else None,
                }
            )
        if tool == "get_available_credit":
            return tool_result(self.credit[arguments["card_id"]])
        card = next(c for c in self.cards() if c["card_id"] == arguments["card_id"])
        if tool == "get_card":
            return tool_result({"outcome": "ok", **stamped, "card": detail(card)})
        if self.verified:
            self.blocked.add(card["card_id"])
        return tool_result(
            {
                "outcome": "ok",
                **stamped,
                "card_id": card["card_id"],
                "reason": arguments["reason"],
                "confirmation_id": arguments["confirmation_id"],
                "attempts": 1 if self.verified else 3,
                "block_outcome": "verified" if self.verified else "not_verified",
                "read_back": "Blocked" if self.verified else "Active",
                "repeated": False,
            }
        )


def detail(card: dict[str, Any]) -> dict[str, Any]:
    """
    A listed card as get_card reads it; one past its expiration has one recorded.
    """
    return {
        **card,
        "opening_date": "2023-02-14",
        "expiration_date": "2026-02-13" if card["past_expiration"] else None,
    }


# The scripted model's answer about every card, and what the customer reads once it is filled in (POL-14, POL-31).
CARDS_ANSWER = "Estas son sus tarjetas:\n\n{cards}"


def cards_answer(bank: "Bank", language: str = "es") -> str:
    shown = [detail(c) for c in bank.cards()]
    conflicts = [
        render("past_expiration", language, {"card": c})
        for c in shown
        if c["product_status"] == "Active" and c["past_expiration"]
    ]
    return "\n\n".join(
        [fill(CARDS_ANSWER, values(language, {"statuses": shown})), *conflicts]
    )


def tool_result(output: Any) -> httpx.Response:
    text = output if isinstance(output, str) else json.dumps(output)
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": text}], "isError": False},
        },
    )


@dataclass(frozen=True)
class Customer:
    sub: str
    origin_jti: str
    customer_id: str
    groups: tuple[str, ...] = ("customer",)
    client_id: str = CLIENT_ID

    def token(self, **overrides: Any) -> str:
        claims: dict[str, Any] = {
            "sub": self.sub,
            "origin_jti": self.origin_jti,
            "customer_id": self.customer_id,
            "cognito:groups": list(self.groups),
            "token_use": "access",
            "client_id": self.client_id,
            **overrides,
        }
        claims = {k: v for k, v in claims.items() if v is not None}

        def part(value: dict[str, Any]) -> str:
            return (
                base64.urlsafe_b64encode(json.dumps(value).encode())
                .decode()
                .rstrip("=")
            )

        return f"{part({'alg': 'none'})}.{part(claims)}.signature"


def customer(customer_id: str = "CLI-EXAMPLE00001") -> Customer:
    return Customer(str(uuid.uuid4()), str(uuid.uuid4()), customer_id)


class MemoryRecords:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.fail_on: str | None = None

    def put(self, entry: dict[str, Any]) -> None:
        if entry["kind"] == self.fail_on:
            raise RuntimeError("the record store is down")
        assert all(e["entry_key"] != entry["entry_key"] for e in self.entries)
        self.entries.append(entry)

    def of(self, sign_in: str) -> list[dict[str, Any]]:
        return sorted(
            (e for e in self.entries if e["sign_in"] == sign_in),
            key=lambda e: e["entry_key"],
        )


class Claimed:
    """
    Cognito's GetUser, answered from the token's own claims.
    """

    def verify(self, token: str) -> Caller | None:
        claims = claims_of(token)
        user = {
            "UserAttributes": [
                {"Name": "sub", "Value": claims.get("sub")},
                {"Name": "custom:customer_id", "Value": claims.get("customer_id")},
            ]
        }
        return caller_of(token, user, CLIENT_ID)


class RecordedTurns:
    def __init__(self, records: "MemoryRecords") -> None:
        self.records = records

    def turns(
        self, sign_in: str, prefixes: Any, attributes: Any = None
    ) -> list[dict[str, Any]]:
        found = [
            e
            for e in self.records.of(sign_in)
            if any(e["entry_key"].startswith(f"{p}#") for p in prefixes)
        ]
        if attributes is None:
            return found
        return [{k: e[k] for k in attributes if k in e} for e in found]


class FileHandoffLambda:
    """
    Invokes file_handoff's handler in process; failures, while any are left, answer as a function error.
    """

    def __init__(self, stores: HandoffStores, now: Callable[[], datetime]) -> None:
        self.stores = stores
        self.now = now
        self.failures = 0
        self.events: list[dict[str, Any]] = []

    def invoke(self, **request: Any) -> dict[str, Any]:
        event = json.loads(request["Payload"])
        self.events.append(event)
        metadata = {"RequestId": uuid.uuid4().hex}
        if self.failures:
            self.failures -= 1
            return {
                "FunctionError": "Unhandled",
                "Payload": io.BytesIO(b'{"errorMessage": "failed"}'),
                "ResponseMetadata": metadata,
            }
        output = handoff.answer(event, lambda: self.stores, self.now)
        return {
            "Payload": io.BytesIO(json.dumps(output).encode()),
            "ResponseMetadata": metadata,
        }


class MemoryBindings:
    def __init__(self) -> None:
        self.bound: dict[str, str] = {}

    def bind(self, session_id: str, sub: str, at: datetime) -> bool:
        return self.bound.setdefault(session_id, sub) == sub


@dataclass
class Script:
    requests: list[str] = field(default_factory=lambda: ["card_status"])
    has_request: bool = True
    complaint: bool = False
    kind: str = "other"
    # What the model says the message's language is; unclear keeps the conversation's (POL-50).
    language: str = "unclear"
    route_error: Exception | None = None
    # The default request asks about all the customer's cards (POL-14).
    extracted: dict[str, Any] = field(default_factory=lambda: {"cards": "all"})
    extract_error: Exception | None = None
    # One per answer the model writes, in order; the last repeats.
    replies: list[str] = field(default_factory=lambda: [CARDS_ANSWER])
    reply_error: Exception | None = None
    handoff_text: dict[str, Any] = field(
        default_factory=lambda: {
            "summary": "El cliente pidió hablar con una persona del banco.",
            "customer_statements": ["El cliente quiere que lo atienda una persona."],
            "unresolved_questions": [],
        }
    )
    handoff_error: Exception | None = None
    fitting: list[int] = field(default_factory=lambda: [1])
    choose_error: Exception | None = None
    gateway: Callable[[httpx.Request], httpx.Response] | None = None
    model_inputs: dict[str, list[list[BaseMessage]]] = field(
        default_factory=lambda: {
            "route": [],
            "extract": [],
            "reply": [],
            "choose": [],
            "handoff_text": [],
        }
    )
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.script = Script()
        self.records = MemoryRecords()
        self.bindings = MemoryBindings()
        self.saver = InMemorySaver()
        self.graph = build(self.saver)
        self.bank = Bank()
        self.confirmations = MemoryConfirmations()
        self.moved = timedelta()
        self.workload_tokens: list[str | None] = []
        self.key_error: Exception | None = None
        now = lambda: datetime.now(UTC) + self.moved  # noqa: E731
        data = tools_data_items()
        self.cases = MemoryCases()
        self.lambda_client = FileHandoffLambda(
            HandoffStores(
                data=MemoryData(data),
                flags=MemoryFlags(data),
                cases=self.cases,
                verifier=Claimed(),
                records=RecordedTurns(self.records),
            ),
            now,
        )
        transport = httpx.MockTransport(self.answer_tool)
        self.services = entrypoint.Services(
            settings=SETTINGS,
            graph=self.graph,
            bindings=self.bindings,
            records=self.records,
            fetch_key=self.fetch_key,
            models=self.factory,
            gateway=Gateway(
                SETTINGS.gateway_url,
                SETTINGS.gateway_targets,
                httpx.AsyncClient(transport=transport),
            ),
            confirmations=self.confirmations,
            filing=Filing(SETTINGS.file_handoff_function, self.lambda_client, now),
            now=now,
            retries=Retries(sleep=self.slept),
        )
        self.waits: list[float] = []
        monkeypatch.setattr(entrypoint, "services", lambda: self.services)

    async def slept(self, seconds: float) -> None:
        self.waits.append(seconds)

    async def fetch_key(self, token: str | None) -> str:
        self.workload_tokens.append(token)
        if self.key_error is not None:
            raise self.key_error
        return "model-key"

    def answer_tool(self, request: httpx.Request) -> httpx.Response:
        self.script.tool_calls.append(json.loads(request.content))
        if self.script.gateway is not None:
            return self.script.gateway(request)
        return self.bank.answer(request)

    def factory(self, key: str) -> Factory:
        assert key == "model-key"
        script = self.script

        async def route(messages: list[BaseMessage]) -> dict[str, Any]:
            script.model_inputs["route"].append(messages)
            if script.route_error is not None:
                raise script.route_error
            raw = AIMessage(
                content="{}",
                usage_metadata=USAGE,
                response_metadata={"model_name": MODEL},
            )
            parsed = RouterOutput.model_validate(
                {
                    "requests": script.requests,
                    "has_request": script.has_request,
                    "complaint": script.complaint,
                    "language": script.language,
                    "kind": script.kind,
                }
            )
            return {"raw": raw, "parsed": parsed, "parsing_error": None}

        async def extract(messages: list[BaseMessage]) -> dict[str, Any]:
            script.model_inputs["extract"].append(messages)
            if script.extract_error is not None:
                raise script.extract_error
            raw = AIMessage(
                content="{}",
                usage_metadata=USAGE,
                response_metadata={"model_name": MODEL},
            )
            parsed = RequestDetails.model_validate(
                EXTRACTED | {"language": script.language} | script.extracted
            )
            return {"raw": raw, "parsed": parsed, "parsing_error": None}

        async def choose(messages: list[BaseMessage]) -> dict[str, Any]:
            script.model_inputs["choose"].append(messages)
            if script.choose_error is not None:
                raise script.choose_error
            raw = AIMessage(
                content="{}",
                usage_metadata=USAGE,
                response_metadata={"model_name": MODEL},
            )
            parsed = TransactionChoice.model_validate(
                {
                    "fitting": script.fitting,
                    "language": script.language,
                    "question": script.extracted.get("question"),
                }
            )
            return {"raw": raw, "parsed": parsed, "parsing_error": None}

        async def handoff_text(messages: list[BaseMessage]) -> dict[str, Any]:
            script.model_inputs["handoff_text"].append(messages)
            if script.handoff_error is not None:
                raise script.handoff_error
            raw = AIMessage(
                content="{}",
                usage_metadata=USAGE,
                response_metadata={"model_name": MODEL},
            )
            parsed = HandoffText.model_validate(script.handoff_text)
            return {"raw": raw, "parsed": parsed, "parsing_error": None}

        async def reply(messages: list[BaseMessage]) -> AIMessage:
            script.model_inputs["reply"].append(messages)
            if script.reply_error is not None:
                raise script.reply_error
            content = (
                script.replies.pop(0) if len(script.replies) > 1 else script.replies[0]
            )
            return AIMessage(
                content=content,
                usage_metadata=USAGE,
                response_metadata={"model_name": MODEL},
            )

        def make(purpose: str) -> Runnable[Any, Any]:
            chosen = {
                "route": route,
                "extract": extract,
                "choose": choose,
                "handoff_text": handoff_text,
            }.get(purpose, reply)
            return RunnableLambda(chosen)

        return Declared(make, PROVIDER, MODEL)

    def post(
        self,
        body: dict[str, Any],
        token: str | None,
        session: str | None = None,
        workload: str | None = "workload-token",
    ) -> list[dict[str, Any]]:
        headers = {"Accept": "text/event-stream"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if session is not None:
            headers[SESSION_HEADER] = session
        if workload is not None:
            headers["WorkloadAccessToken"] = workload
        with TestClient(entrypoint.app) as client:
            response = client.post("/invocations", json=body, headers=headers)
        assert response.status_code == 200
        return [
            json.loads(line.removeprefix("data:"))
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]

    def checkpoint(self, who: Customer, client_thread_id: str) -> dict[str, Any]:
        key = entrypoint.thread_key(who.sub, client_thread_id)
        state = self.graph.get_state({"configurable": {"thread_id": key}})
        values: dict[str, Any] = state.values
        return values


def session_id() -> str:
    return f"session-{uuid.uuid4().hex}"


def run_body(
    text: str | None = "¿Cuáles son mis tarjetas?",
    thread: str = "thread-0001",
    message_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    messages = (
        []
        if text is None
        else [{"id": message_id or uuid.uuid4().hex, "role": "user", "content": text}]
    )
    return {
        "threadId": thread,
        "runId": f"run-{uuid.uuid4().hex[:12]}",
        "messages": messages,
        **extra,
    }


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    yield Harness(monkeypatch)
