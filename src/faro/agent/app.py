"""
The Runtime's entrypoint (ADR-0004, A turn, end to end, step 3, and its amendments). Before the graph runs, it reads the
token's claims, binds the runtime session to its user, checks the request against the chat's contract, counts a message
or a resume against decision 21's limits, replaces the
thread ID with a key derived from the user, masks the new message, takes a control's answer only when it answers the
thread's pending control, turns a message typed while one is pending into a resume, opens the turn's execution record,
and fetches the model key. The wrapper gets the thread, the run, and the masked message or the one resume alone, and
each event it sends back is rebuilt to the chat's contract. When the run ends, the entrypoint records the controls it
ended at, then the graph's decisions, one per request it served, then closes the turn, before the run's last event. A refused request gets a lone
RUN_ERROR and is recorded in the caller's own sign-in.
"""

import asyncio
import json
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any

import boto3
from ag_ui.core import (
    BaseEvent,
    EventType,
    ResumeEntry,
    RunAgentInput,
    RunFinishedEvent,
    RunStartedEvent,
    UserMessage,
)
from ag_ui_langgraph import LangGraphAgent
from bedrock_agentcore.runtime import AGUIApp
from bedrock_agentcore.runtime.context import BedrockAgentCoreContext, RequestContext
from bedrock_agentcore.services.identity import IdentityClient
from botocore.config import Config
from langgraph.graph.state import CompiledStateGraph
from langgraph_checkpoint_aws import DynamoDBSaver

from faro.agent import metrics
from faro.agent.claims import Claims, ClaimsRefusedError, read_claims, token_of
from faro.agent.confirmations import Confirmations, DynamoConfirmations
from faro.agent.events import SUCCESS, checked, rebuild, run_error
from faro.agent.filing import Filing
from faro.agent.gateway import Gateway
from faro.agent.graph import build
from faro.agent.language import DEFAULT
from faro.agent.models import (
    Factory,
    Models,
    anthropic_factory,
    prompt_version,
)
from faro.agent.records import DynamoRecords, RecordStore, Turn
from faro.agent.request import (
    NewMessage,
    Pending,
    RequestRefusedError,
    Resume,
    Warmup,
    check_contract,
    read,
)
from faro.agent.retries import RETRIES, Retries
from faro.agent.scope import SCOPE, Scope
from faro.agent.sessions import Bindings, DynamoBindings
from faro.agent.usage import DynamoUsage, Limits, Usage
from faro.contracts import NAMES, version
from faro.policy import POLICY_VERSION

# Spike S4 saw the workload access token under either name, depending on the SDK that sent it.
WORKLOAD_TOKEN_HEADERS = ("workloadaccesstoken", "x-amz-bedrock-agentcore-identity-wat")
CHECKPOINTS_KEPT = timedelta(days=7)
CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{7,64}$")
# One attempt per invocation: Filing retries under the same call ID and records each attempt.
LAMBDA_CONFIG = Config(
    connect_timeout=5, read_timeout=15, retries={"total_max_attempts": 1}
)

logger = logging.getLogger(__name__)
app = AGUIApp()


@dataclass(frozen=True)
class Settings:
    client_id: str
    gateway_url: str
    gateway_targets: dict[str, str]
    file_handoff_function: str
    stamp: dict[str, str]
    clock: dict[str, str]
    app_version: str

    @classmethod
    def from_env(cls) -> "Settings":
        tools_data = json.loads(os.environ["TOOLS_DATA"])
        return cls(
            client_id=os.environ["CUSTOMER_CLIENT_ID"],
            gateway_url=os.environ["GATEWAY_URL"],
            gateway_targets=json.loads(os.environ["GATEWAY_TARGETS"]),
            file_handoff_function=os.environ["FILE_HANDOFF_FUNCTION"],
            stamp=tools_data["stamp"],
            clock=tools_data["clock"],
            app_version=os.environ["APP_VERSION"],
        )


@dataclass(frozen=True)
class Services:
    settings: Settings
    graph: CompiledStateGraph[Any, Any, Any, Any]
    bindings: Bindings
    records: RecordStore
    fetch_key: Callable[[str | None], Awaitable[str]]
    models: Callable[[str], Factory]
    gateway: Gateway
    confirmations: Confirmations
    filing: Filing
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    # Decision 18's waits; the evaluation's in-process player passes one that doesn't sleep.
    retries: Retries = RETRIES
    # Decision 21's counters; None counts nothing, as in the in-process player, whose turns take milliseconds.
    usage: Usage | None = None


def workload_token(headers: Mapping[str, str]) -> str | None:
    return next((headers[h] for h in WORKLOAD_TOKEN_HEADERS if headers.get(h)), None)


async def fetch_model_key(token: str | None) -> str:
    if not token:
        raise RuntimeError("no workload access token in the request")
    region = os.environ.get("AWS_REGION", "us-east-1")
    key: str = await IdentityClient(region).get_api_key(
        provider_name=os.environ["MODEL_KEY_PROVIDER"], agent_identity_token=token
    )
    return key


@cache
def services() -> Services:
    region = os.environ.get("AWS_REGION", "us-east-1")
    settings = Settings.from_env()
    saver = DynamoDBSaver(
        table_name=os.environ["CHECKPOINTS_TABLE"],
        region_name=region,
        ttl_seconds=int(CHECKPOINTS_KEPT.total_seconds()),
    )
    records = boto3.resource("dynamodb", region_name=region).Table(
        os.environ["EXECUTION_RECORDS_TABLE"]
    )
    client = boto3.client("dynamodb", region_name=region)
    return Services(
        settings=settings,
        graph=build(saver),
        bindings=DynamoBindings(client, os.environ["SESSION_BINDINGS_TABLE"]),
        records=DynamoRecords(records),
        fetch_key=fetch_model_key,
        models=anthropic_factory,
        gateway=Gateway(settings.gateway_url, settings.gateway_targets),
        confirmations=DynamoConfirmations(client, os.environ["CONFIRMATIONS_TABLE"]),
        filing=Filing(
            settings.file_handoff_function,
            boto3.client("lambda", region_name=region, config=LAMBDA_CONFIG),
        ),
        usage=DynamoUsage(
            client,
            os.environ["USAGE_COUNTERS_TABLE"],
            Limits(
                int(os.environ["TURNS_PER_MINUTE"]), int(os.environ["TURNS_PER_DAY"])
            ),
        ),
    )


def thread_key(sub: str, client_thread_id: str) -> str:
    """
    Another user's thread ID names a different, empty thread (decision 12).
    """
    return str(uuid.uuid5(uuid.UUID(sub), client_thread_id))


def pending_of(snapshot: Any) -> Pending | None:
    interrupts = getattr(snapshot, "interrupts", ()) or ()
    if not interrupts:
        return None
    value = interrupts[0].value
    return Pending(interrupts[0].id, value.get("controls", []))


def recorded_input(parsed: NewMessage | Warmup | Resume) -> dict[str, Any]:
    if isinstance(parsed, NewMessage):
        return {"kind": "message", "text": parsed.text}
    if isinstance(parsed, Resume):
        answer = {k: v for k, v in parsed.payload.items() if k != "message_id"}
        return {"kind": "resume", "resume": answer}
    return {"kind": "warmup"}


def recorded_id(value: str | None) -> str | None:
    """
    IDs are recorded as the client drew them, since a masked ID would no longer join the record to AgentCore's logs;
    a value that isn't ID-shaped isn't recorded at all.
    """
    return value if value and CLIENT_ID.match(value) else None


class Entrypoint:
    def __init__(self, services: Services) -> None:
        self.services = services

    def versions(self) -> dict[str, Any]:
        settings = self.services.settings
        return {
            "app": settings.app_version,
            "policy": POLICY_VERSION,
            "prompts": {
                name: prompt_version(name)
                for name in (
                    "route",
                    "resolve_card",
                    "find_transaction",
                    "reply",
                    "handoff",
                )
            },
            "schemas": {name: version(name) for name in NAMES},
            "snapshot": settings.stamp["snapshot"],
            "pipeline_version": settings.stamp["pipeline_version"],
        }

    async def refuse(
        self,
        code: str,
        started: datetime,
        sign_in: str | None,
        sub: str | None,
        source: str,
        session_id: str | None,
        client_thread_id: str | None,
        errors: list[dict[str, str]] | None = None,
    ) -> BaseEvent:
        if sign_in is None:
            logger.warning(
                "refused a request as %s without a sign-in to record it in", code
            )
            return run_error(code)
        fields: dict[str, Any] = {
            "code": code,
            "sub": sub,
            "runtime_session_id": session_id
            if session_id and len(session_id) <= 256
            else None,
            "client_thread_id": recorded_id(client_thread_id),
        }
        if errors:
            fields["errors"] = errors
        turn = Turn(self.services.records, sign_in, source, started, self.services.now)
        try:
            await turn.write("request_refused", **fields)
        except Exception:
            logger.exception("couldn't record a request refused as %s", code)
        return run_error(code)

    async def run(
        self, run_input: RunAgentInput, context: RequestContext
    ) -> AsyncIterator[BaseEvent]:
        services = self.services
        started = services.now()
        authorization = (context.request_headers or {}).get("Authorization")
        session_id = context.session_id
        try:
            claims = read_claims(authorization, services.settings.client_id)
        except ClaimsRefusedError as refusal:
            yield await self.refuse(
                refusal.code,
                started,
                refusal.origin_jti,
                refusal.sub,
                refusal.source,
                session_id,
                run_input.thread_id,
            )
            return

        async def refused(
            code: str, errors: list[dict[str, str]] | None = None
        ) -> BaseEvent:
            return await self.refuse(
                code,
                started,
                claims.origin_jti,
                claims.sub,
                claims.source,
                session_id,
                run_input.thread_id,
                errors,
            )

        if not session_id or not await asyncio.to_thread(
            services.bindings.bind, session_id, claims.sub, started
        ):
            yield await refused("session_refused")
            return
        request = run_input.model_dump(by_alias=True, exclude_none=True, mode="json")
        try:
            check_contract(request)
            key = thread_key(claims.sub, run_input.thread_id)
            snapshot = await services.graph.aget_state(
                {"configurable": {"thread_id": key}}
            )
            held = {m.id for m in snapshot.values.get("messages", [])}
            parsed = read(request, held, pending_of(snapshot))
        except RequestRefusedError as refusal:
            yield await refused("invalid_request", refusal.errors)
            return

        # The warm-up runs no graph, so it isn't counted (decision 20).
        if services.usage is not None and not isinstance(parsed, Warmup):
            limited = await asyncio.to_thread(
                services.usage.count, claims.origin_jti, claims.sub, started
            )
            if limited is not None:
                yield await refused(limited)
                return

        turn = Turn(
            services.records, claims.origin_jti, claims.source, started, services.now
        )
        headers = context.request.headers if context.request is not None else {}
        async for event in self.turn(
            turn, claims, token_of(authorization), key, run_input, parsed,
            snapshot.values.get("language", DEFAULT), session_id, workload_token(headers),
        ):  # fmt: skip
            yield event

    async def turn(
        self,
        turn: Turn,
        claims: Claims,
        token: str,
        key: str,
        run_input: RunAgentInput,
        parsed: NewMessage | Warmup | Resume,
        language: str,
        session_id: str,
        workload: str | None,
    ) -> AsyncIterator[BaseEvent]:
        services = self.services
        finished = False
        try:
            await turn.write(
                "turn_opened",
                sub=claims.sub,
                customer_id=claims.customer_id,
                role="customer",
                thread_key=key,
                client_thread_id=run_input.thread_id,
                client_run_id=run_input.run_id,
                runtime_session_id=session_id,
                request_id=BedrockAgentCoreContext.get_request_id()
                or str(uuid.uuid4()),
                input=recorded_input(parsed),
                language=language,
                clock=services.settings.clock,
                versions=self.versions(),
            )
            logger.info("turn %s opened", turn.turn_id)
            if isinstance(parsed, Warmup):
                yield checked(
                    RunStartedEvent(
                        thread_id=run_input.thread_id, run_id=run_input.run_id
                    )
                )
                await self.close(turn, "finished")
                finished = True
                yield checked(
                    RunFinishedEvent(
                        thread_id=run_input.thread_id,
                        run_id=run_input.run_id,
                        outcome=SUCCESS,
                    )
                )
                return
            model_key = await services.fetch_key(workload)
            SCOPE.set(
                Scope(
                    claims=claims,
                    token=token,
                    thread_key=key,
                    turn=turn,
                    gateway=services.gateway,
                    models=Models(
                        services.models(model_key),
                        turn.write,
                        services.retries,
                        lambda: turn.latency_ms() / 1000,
                    ),
                    confirmations=services.confirmations,
                    filing=services.filing,
                    snapshot=services.settings.stamp["snapshot"],
                    business_date=services.settings.clock["business_date"],
                    as_of=services.settings.clock["as_of"],
                    now=services.now,
                    retries=services.retries,
                )
            )
            wrapper = LangGraphAgent(
                name="card_support",
                graph=services.graph,
                emit_raw_events=False,
                enable_legacy_on_interrupt_event=False,
                emit_interrupt_outcome=True,
            )
            given = RunAgentInput(
                thread_id=key,
                run_id=run_input.run_id,
                messages=(
                    [UserMessage(id=parsed.id, role="user", content=parsed.text)]
                    if isinstance(parsed, NewMessage)
                    else []
                ),
                state={},
                tools=[],
                context=[],
                forwarded_props={},
                resume=(
                    [
                        ResumeEntry(
                            interrupt_id=parsed.interrupt_id,
                            status="resolved",
                            payload=parsed.payload,
                        )
                    ]
                    if isinstance(parsed, Resume)
                    else None
                ),
            )
            async for event in wrapper.run(given):
                rebuilt = rebuild(event, run_input.thread_id, run_input.run_id)
                if rebuilt is None:
                    continue
                rebuilt = checked(rebuilt)
                if isinstance(rebuilt, RunFinishedEvent):
                    await self.finish(turn, rebuilt)
                    finished = True
                yield rebuilt
            if not finished:
                raise RuntimeError("the wrapper ended without finishing the run")
        except Exception:
            logger.exception("the turn failed")
            if finished:
                return
            try:
                await self.close(turn, "error")
            except Exception:
                logger.exception("couldn't close a failed turn's record")
                turn.emit("request_failed")
            yield run_error("internal")

    async def finish(self, turn: Turn, event: RunFinishedEvent) -> None:
        interrupts = getattr(event.outcome, "interrupts", None) or []
        for interrupt in interrupts:
            await turn.write(
                "interrupt",
                interrupt_id=interrupt.id,
                controls=[
                    {
                        k: control[k]
                        for k in ("kind", "confirmation_id", "offer_id", "reason_code")
                        if k in control
                    }
                    for control in (interrupt.metadata or {})["controls"]
                ],
            )
        if not turn.decisions:
            raise RuntimeError("the graph ended its run without a decision")
        for decision in turn.decisions:
            await turn.write("decision", **decision)
        await self.close(turn, "interrupted" if interrupts else "finished")

    async def close(self, turn: Turn, outcome: str) -> None:
        fields: dict[str, Any] = {
            "outcome": outcome,
            "latency_ms": turn.latency_ms(),
            "totals": turn.totals(),
        }
        if outcome == "error":
            fields["error_code"] = "internal"
        await turn.write("turn_closed", **fields)
        logger.info("turn %s closed: %s", turn.turn_id, outcome)


async def handler(
    run_input: RunAgentInput, context: RequestContext
) -> AsyncIterator[BaseEvent]:
    """
    An exception must not reach the SDK, which would send its message to the chat under a code outside the contract.
    """
    ended = False
    try:
        async for event in Entrypoint(services()).run(run_input, context):
            yield event
            ended = event.type in (EventType.RUN_FINISHED, EventType.RUN_ERROR)
    except Exception:
        logger.exception("the request failed before its turn was opened")
        metrics.emit("request_failed", "unknown", None)
        if not ended:
            yield run_error("internal")


app.entrypoint(handler)


def main(app_version: str = "0" * 40) -> None:
    # The SDK's AG-UI app configures no logging; a turn's ID ties its log lines to its record (OPS-01).
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    metrics.configure()
    os.environ["APP_VERSION"] = app_version
    app.run()
