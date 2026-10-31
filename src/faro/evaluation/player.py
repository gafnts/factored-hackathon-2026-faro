"""
The in-process player (ADR-0005's amendments of 2026-10-01): a case played through the Runtime's own entrypoint, the
wrapper, and the graph in this process, with nothing in the agent changed. Each case gets a stack of its own: the
tools' library over the export's items for its customer, Cedar's rule checked on every tool call before the tools
run (the token's customer and sign-in must be the call's), file_handoff's code over in-memory cases, and in-memory
checkpoints, bindings, records, and confirmations, which the block tool reads as the Runtime wrote them. Only the
models change, to the factory the caller gives: scripted ones for CI, the baseline's; and decision 18's retries take no
waits, since a case's latency isn't measured here. Tokens are unsigned, since the entrypoint never checks a signature
the Runtime's authorizer already checked, and carry the evaluation group, so the record says source=evaluation;
file_handoff's check of a token through Cognito becomes a check of its claims.

A case's fixtures and fault plans are written to the sandbox under its sign-in before its first turn, as the harness
writes them to the deployed overlay. The evidence is what the harness keeps for a case played end to end: each turn's
events with their arrival times, the sign-in's record, the sandbox's end state, and the handoff cases filed.
"""

import base64
import io
import json
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from ag_ui.core import EventType, RunAgentInput
from bedrock_agentcore.runtime.context import RequestContext
from langgraph.checkpoint.memory import InMemorySaver

from faro.agent.app import Entrypoint, Services, Settings
from faro.agent.claims import CUSTOMER_GROUP, EVALUATION_GROUP
from faro.agent.confirmations import MemoryConfirmations
from faro.agent.events import run_error
from faro.agent.filing import Filing
from faro.agent.gateway import DENIED, Gateway
from faro.agent.graph import build
from faro.agent.models import Factory
from faro.agent.retries import Retries, no_wait
from faro.evaluation import cases
from faro.evaluation.customer import Customer, ScriptError, Send
from faro.tools import block, handoff, reads
from faro.tools.cases import MemoryCases, MemoryFlags
from faro.tools.file_handoff import HandoffStores
from faro.tools.identity import Caller, caller_of, claims_of
from faro.tools.sandbox import KEPT, BlockStores, MemorySandbox, Stores
from faro.tools.store import MemoryData

CLIENT_ID = "evaluation-player"
TARGETS = {
    "list_cards": "reads",
    "get_card": "reads",
    "get_available_credit": "reads",
    "find_transactions": "reads",
    "block_card": "block",
}
GATEWAY_URL = "https://gateway.invalid/mcp"
FILE_HANDOFF = "file-handoff"


def unsigned(claims: Mapping[str, Any]) -> str:
    def part(value: Mapping[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}.unsigned"


def rpc(body: Mapping[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, **body})


def tool_result(output: Any, failed: bool = False) -> httpx.Response:
    text = json.dumps(output)
    return rpc(
        {"result": {"content": [{"type": "text", "text": text}], "isError": failed}}
    )


class Records:
    """
    The execution record, as the Runtime writes it and file_handoff reads the turns a case cites.
    """

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def put(self, entry: dict[str, Any]) -> None:
        if any(e["entry_key"] == entry["entry_key"] for e in self.entries):
            raise RuntimeError("an entry with that key exists")
        self.entries.append(json.loads(json.dumps(entry)))

    def of(self, sign_in: str) -> list[dict[str, Any]]:
        return sorted(
            (e for e in self.entries if e["sign_in"] == sign_in),
            key=lambda e: e["entry_key"],
        )

    def turns(
        self,
        sign_in: str,
        prefixes: Sequence[str],
        attributes: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        found = [
            e
            for e in self.of(sign_in)
            if any(e["entry_key"].startswith(f"{p}#") for p in prefixes)
        ]
        if attributes is None:
            return found
        return [{k: e[k] for k in attributes if k in e} for e in found]


class Bindings:
    def __init__(self) -> None:
        self.bound: dict[str, str] = {}

    def bind(self, session_id: str, sub: str, at: datetime) -> bool:
        return self.bound.setdefault(session_id, sub) == sub


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


class Stack:
    """
    One case's stack, and the Gateway and file_handoff's Lambda as the Runtime reaches them.
    """

    def __init__(self, items: Iterable[Mapping[str, Any]], models: Factory) -> None:
        held = [dict(i) for i in items]
        meta = next(i for i in held if i["pk"] == "META")
        data = MemoryData(held)
        self.records = Records()
        self.confirmations = MemoryConfirmations()
        self.sandbox = MemorySandbox()
        # The block tool reads and changes the confirmations the Runtime created, as both do in one table.
        self.sandbox.confirmations = self.confirmations.records
        self.cases = MemoryCases()
        self.reads = Stores(data=data, overlay=self.sandbox)
        self.block = BlockStores(data=data, sandbox=self.sandbox)
        self.handoff = HandoffStores(
            data=data,
            flags=MemoryFlags(held),
            cases=self.cases,
            verifier=Claimed(),
            records=self.records,
        )
        self.now = lambda: datetime.now(UTC)
        settings = Settings(
            client_id=CLIENT_ID,
            gateway_url=GATEWAY_URL,
            gateway_targets=TARGETS,
            file_handoff_function=FILE_HANDOFF,
            stamp=meta["stamp"],
            clock=meta["clock"],
            app_version="0" * 40,
        )
        self.services = Services(
            settings=settings,
            graph=build(InMemorySaver()),
            bindings=Bindings(),
            records=self.records,
            fetch_key=self.fetch_key,
            models=lambda key: models,
            gateway=Gateway(
                GATEWAY_URL,
                TARGETS,
                httpx.AsyncClient(transport=httpx.MockTransport(self.gateway)),
            ),
            confirmations=self.confirmations,
            filing=Filing(FILE_HANDOFF, self, self.now),
            now=self.now,
            retries=Retries(sleep=no_wait),
        )

    async def fetch_key(self, token: str | None) -> str:
        return "scripted"

    def gateway(self, request: httpx.Request) -> httpx.Response:
        """
        Cedar's rule first, as the policy engine applies it before the target runs (ADR-0004; infra/modules/gateway).
        """
        params = json.loads(request.content)["params"]
        target, _, tool = params["name"].rpartition("___")
        arguments = params["arguments"]
        if TARGETS.get(tool) != target:
            return rpc({"error": {"code": -32602, "message": "Unknown tool"}})
        token = request.headers["Authorization"].removeprefix("Bearer ")
        claims = claims_of(token)
        if not isinstance(arguments, dict) or any(
            claims.get(k) is None or claims.get(k) != arguments.get(k)
            for k in ("customer_id", "origin_jti")
        ):
            return rpc({"error": {"code": DENIED, "message": "Denied"}})
        try:
            if tool == "block_card":
                output = block.answer(arguments, lambda: self.block, self.now)
            else:
                output = reads.answer(tool, arguments, lambda: self.reads)
        except Exception:
            return tool_result("An error occurred", failed=True)
        return tool_result(output)

    def invoke(self, **request: Any) -> dict[str, Any]:
        """
        file_handoff's Lambda, as Filing invokes it.
        """
        event = json.loads(request["Payload"])
        metadata = {"RequestId": uuid.uuid4().hex}
        try:
            output = handoff.answer(event, lambda: self.handoff, self.now)
        except Exception:
            return {
                "FunctionError": "Unhandled",
                "Payload": io.BytesIO(b'{"errorMessage": "failed"}'),
                "ResponseMetadata": metadata,
            }
        return {
            "Payload": io.BytesIO(json.dumps(output).encode()),
            "ResponseMetadata": metadata,
        }


class SignIn:
    """
    The case's test user and one sign-in: the claims the pre-token trigger would add, under the evaluation group.
    """

    def __init__(self, customer_id: str) -> None:
        self.sub = str(uuid.uuid4())
        self.origin_jti = str(uuid.uuid4())
        self.token = unsigned(
            {
                "sub": self.sub,
                "origin_jti": self.origin_jti,
                "customer_id": customer_id,
                "cognito:groups": [CUSTOMER_GROUP, EVALUATION_GROUP],
                "token_use": "access",
                "client_id": CLIENT_ID,
            }
        )
        self.session = f"eval-session-{uuid.uuid4().hex}"
        self.thread = f"eval-thread-{uuid.uuid4().hex}"


async def turn(stack: Stack, who: SignIn, send: Send) -> list[dict[str, Any]]:
    body: dict[str, Any] = {
        "threadId": who.thread,
        "runId": f"run-{uuid.uuid4().hex[:12]}",
        "messages": [],
    }
    if send.resume is not None:
        body["resume"] = [send.resume]
    else:
        body["messages"] = [
            {"id": uuid.uuid4().hex, "role": "user", "content": send.text}
        ]
    context = RequestContext(
        session_id=who.session,
        request_headers={"Authorization": f"Bearer {who.token}"},
        request=None,
    )
    started = time.perf_counter()
    received: list[dict[str, Any]] = []
    ended = False
    try:
        async for event in Entrypoint(stack.services).run(
            RunAgentInput.model_validate(body), context
        ):
            received.append(timed(event, started))
            ended = event.type in (EventType.RUN_FINISHED, EventType.RUN_ERROR)
    except Exception:
        # As the Runtime's handler answers a request that failed before its turn was opened.
        if not ended:
            received.append(timed(run_error("internal"), started))
    return received


def timed(event: Any, started: float) -> dict[str, Any]:
    return {
        "at_ms": round((time.perf_counter() - started) * 1000),
        "event": event.model_dump(by_alias=True, exclude_none=True, mode="json"),
    }


def ended_at(events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    last = events[-1]["event"] if events else {}
    outcome = last.get("outcome") or {}
    return outcome["interrupts"][0] if outcome.get("type") == "interrupt" else None


async def play(
    case: Mapping[str, Any], items: Iterable[Mapping[str, Any]], models: Factory
) -> dict[str, Any]:
    """
    Plays one case to its end and returns its evidence. A case the player can't play as written is its error, and
    is recorded as such, never graded.
    """
    stack = Stack(items, models)
    who = SignIn(case["customer_id"])
    kept_until = int((datetime.now(UTC) + KEPT).timestamp())
    for item in cases.written(case, who.origin_jti, kept_until):
        stack.sandbox.put(item)
    turns: list[dict[str, Any]] = []
    error = None
    try:
        customer = Customer(case)
        send: Send | None = customer.first()
        while send is not None:
            seen = len(stack.records.of(who.origin_jti))
            events = await turn(stack, who, send)
            entries = stack.records.of(who.origin_jti)[seen:]
            decisions = [e for e in entries if e["kind"] == "decision"]
            turns.append(
                {"sends": send.sends, "text_id": send.text_id, "events": events}
            )
            awaiting = decisions[-1]["awaiting"] if decisions else None
            send = customer.next(awaiting, ended_at(events))
    except ScriptError as failed:
        error = f"script: {failed}"
    unplaced = getattr(models, "unplaced", None)
    if error is None and unplaced:
        error = "script: a model read a text the case doesn't hold"
    return {
        "case_id": case["case_id"],
        "customer_id": case["customer_id"],
        "sign_ins": [who.origin_jti],
        "turns": turns,
        "record": stack.records.of(who.origin_jti),
        "sandbox": {
            "overlay": [dict(v) for v in stack.sandbox.items.values()],
            "confirmations": [dict(v) for v in stack.confirmations.records.values()],
        },
        "cases": [dict(v) for v in stack.cases.items.values()],
        "error": error,
    }
