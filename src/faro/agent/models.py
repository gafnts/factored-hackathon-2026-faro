"""
The graph's model calls: Claude Haiku 4.5 through Anthropic's API with the key from AgentCore Identity (ADR-0004,
Models, as amended). The router's, the extraction's, and the handoff text's outputs are typed through structured output,
each field among the values its step allows, and a reply is plain text read whole.
Every attempt is recorded as a model_call entry, and runs with emit-messages and emit-tool-calls off and streaming
disabled, so nothing it writes reaches the chat unchecked (ADR-0004, What the chat receives). A call is tried again as
decision 18 says (retries.py), and the client's own retries are off, since each attempt is recorded.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from typing import Any, Literal, Protocol

import anthropic
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ConfigDict, Field

from faro.agent.retries import RETRIES, Retries, Wait, retry_after

MODEL = "claude-haiku-4-5-20251001"
PROVIDER = "anthropic"
MAX_TOKENS = 2048
TEMPERATURE = 0.0
TIMEOUT_S = 30.0
# USD per million tokens at list price on 2026-09-29: input, output, cache write, cache read.
PRICES = {MODEL: (1.00, 5.00, 1.25, 0.10)}
EMIT_OFF = {"emit-messages": False, "emit-tool-calls": False}
# A 4xx (error) would only be repeated (decision 18).
RETRIED = (
    "rate_limited",
    "server_error",
    "timeout",
    "connection_error",
    "invalid_output",
)
WAITED = ("rate_limited", "server_error")

Label = Literal[
    "card_status",
    "available_credit",
    "recent_transactions",
    "decline_reason",
    "block_card",
    "unrecognized_charge",
    "unsupported",
    "talk_to_human",
]
# POL-05's order.
ORDER: tuple[Label, ...] = (
    "block_card",
    "unrecognized_charge",
    "talk_to_human",
    "decline_reason",
    "card_status",
    "available_credit",
    "recent_transactions",
    "unsupported",
)

# Which language a message is mostly in (POL-50); other gets POL-51's reply (ADR-0004's amendment of 2026-10-02).
Language = Literal["es", "pt", "other", "unclear"]
# What a message with no request is (POL-06): the reply to it depends on this and on nothing else.
NoRequest = Literal["greeting", "about", "thanks", "closing", "other"]

Record = Callable[..., Any]


class Factory(Protocol):
    """
    Makes a model call's runnable for its purpose, and declares the provider and the model it runs, which the record
    names on every call: no provider for ADR-0005's deterministic baseline and scripted models (ADR-0004's amendment
    of 2026-10-02). A factory that declares nothing is refused before any call.
    """

    @property
    def provider(self) -> str | None: ...

    @property
    def model(self) -> str: ...

    def __call__(self, purpose: str) -> Runnable[Any, Any]: ...


@dataclass(frozen=True)
class Declared:
    """
    A function that makes a model call's runnable, with what it declares (Factory).
    """

    make: Callable[[str], Runnable[Any, Any]]
    provider: str | None
    model: str

    def __call__(self, purpose: str) -> Runnable[Any, Any]:
        return self.make(purpose)


class RouterOutput(BaseModel):
    """
    Every supported request the message holds, whether it holds one at all (S5), whether it is a complaint, which
    POL-44 hands off under its own reason code (ADR-0004's amendment of 2026-09-30), which language it is mostly in,
    and, when it holds no request, what it is instead (the amendment of 2026-10-03).
    """

    model_config = ConfigDict(extra="forbid")

    # First, so the model settles the language before it labels (ADR-0004's amendment of 2026-10-02).
    language: Language
    requests: list[Label] = Field(max_length=8)
    has_request: bool
    complaint: bool
    # Optional, so the evaluation's scripted routers need not say; a message they leave unnamed gets the
    # introduction, or the capabilities once the chat has spoken.
    kind: NoRequest = "other"


# The model never sees the name customer_request, which it read as "the customer asked for the block" (D-006).
OTHER_REASON = "other_reason"
REASON_CODES = {OTHER_REASON: "customer_request"}
# POL-35's reasons for a block, as details() gives them to the graph.
BlockReason = Literal["lost", "stolen", "unrecognized_charge", "customer_request"]


class RequestDetails(BaseModel):
    """
    What a message says about the request it holds, each field among the values the step allows and null when the
    message doesn't say: the card (POL-13), a block's reason (POL-35; any other reason is other_reason to the model, and
    details() gives the graph POL-35's customer_request for it), all the customer's cards (POL-14), the next page or
    an earlier period (POL-25), someone else's card (POL-08), a question about conflicting facts (POL-31), and what an
    unsupported request asks for (POL-41 to POL-43), whether a message sent while the chat waits on its question
    answers it (POL-06), and which language the message is mostly in. Every value is a string, as the execution record
    keeps them.
    """

    model_config = ConfigDict(extra="forbid")

    language: Language
    card_type: Literal["credit", "debit"] | None
    last_four: str | None
    block_reason: (
        Literal["lost", "stolen", "unrecognized_charge", "other_reason"] | None
    )
    cards: Literal["all"] | None
    page: Literal["next", "earlier"] | None
    owner: Literal["someone_else"] | None
    conflict: Literal["asks_which"] | None
    service: (
        Literal[
            "unblock",
            "replacement",
            "pin",
            "limit_increase",
            "other_card_service",
            "outside_cards",
        ]
        | None
    )
    question: Literal["unanswered"] | None

    def details(self) -> dict[str, Any]:
        """
        The fields as the graph reads them, after the call recorded the model's own values: the model's name for any
        other block reason becomes POL-35's code.
        """
        found = self.model_dump()
        found["block_reason"] = REASON_CODES.get(
            found["block_reason"], found["block_reason"]
        )
        return found


class TransactionChoice(BaseModel):
    """
    The transactions listed that fit what the customer says about a charge they don't recognize, by their number in
    the list the model reads, never by ID (POL-27, POL-39), whether a message sent while the chat asks which answers
    it (POL-06), and which language the message is mostly in. Code keeps only numbers in the list.
    """

    model_config = ConfigDict(extra="forbid")

    language: Language
    fitting: list[int] = Field(max_length=10)
    question: Literal["unanswered"] | None


class HandoffText(BaseModel):
    """
    A handoff's free text, in Spanish (POL-46). Its lengths and its text rule are checked in code, which falls back to
    fixed text, so the model's output is never refused for them.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str
    customer_statements: list[str]
    unresolved_questions: list[str]


OUTPUTS: dict[str, type[BaseModel]] = {
    "route": RouterOutput,
    "extract": RequestDetails,
    "choose": TransactionChoice,
    "handoff_text": HandoffText,
}


class ModelFailedError(RuntimeError):
    pass


def prompt(name: str) -> str:
    return (
        files("faro.agent").joinpath(f"prompts/{name}.md").read_text(encoding="utf-8")
    )


def prompt_version(name: str) -> str:
    return sha256(prompt(name).encode("utf-8")).hexdigest()[:16]


def anthropic_factory(key: str) -> Factory:
    def make(purpose: str) -> Runnable[Any, Any]:
        chat = init_chat_model(
            f"{PROVIDER}:{MODEL}",
            api_key=key,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            timeout=TIMEOUT_S,
            max_retries=0,
            disable_streaming=True,
        )
        runnable: Runnable[Any, Any] = (
            chat.with_structured_output(
                OUTPUTS[purpose], method="json_schema", include_raw=True
            )
            if purpose in OUTPUTS
            else chat
        )
        return runnable.with_config(metadata=EMIT_OFF)

    return Declared(make, PROVIDER, MODEL)


def failure(error: Exception) -> str:
    if isinstance(error, anthropic.RateLimitError):
        return "rate_limited"
    if isinstance(error, anthropic.APITimeoutError):
        return "timeout"
    if isinstance(error, anthropic.APIConnectionError):
        return "connection_error"
    if isinstance(error, anthropic.APIStatusError) and error.status_code >= 500:
        return "server_error"
    return "error"


def provider_wait(error: Exception) -> float | None:
    """
    The retry-after a 429 or a 5xx carries.
    """
    if not isinstance(error, anthropic.APIStatusError):
        return None
    return retry_after(error.response.headers)


def usage(message: AIMessage | None) -> dict[str, int | None]:
    metadata: Any = message.usage_metadata if message is not None else None
    if not metadata:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
        }
    details = metadata.get("input_token_details") or {}
    return {
        "input_tokens": metadata.get("input_tokens"),
        "output_tokens": metadata.get("output_tokens"),
        "cache_read_tokens": details.get("cache_read"),
        "cache_write_tokens": details.get("cache_creation"),
    }


def cost(counted: dict[str, int | None]) -> float | None:
    """
    The input count includes cached tokens, which are priced apart.
    """
    if counted["input_tokens"] is None or counted["output_tokens"] is None:
        return None
    price_in, price_out, price_write, price_read = PRICES[MODEL]
    read = counted["cache_read_tokens"] or 0
    written = counted["cache_write_tokens"] or 0
    fresh = counted["input_tokens"] - read - written
    total = (
        fresh * price_in
        + counted["output_tokens"] * price_out
        + written * price_write
        + read * price_read
    )
    return round(total / 1_000_000, 8)


class Models:
    def __init__(
        self,
        factory: Factory,
        record: Record,
        retries: Retries = RETRIES,
        elapsed: Callable[[], float] = lambda: 0.0,
    ) -> None:
        """
        elapsed is how long the turn has run, in seconds, which the retries' deadline reads.
        """
        self.factory = factory
        self.provider = factory.provider
        self.requested = factory.model
        self.record = record
        self.retries = retries
        self.elapsed = elapsed

    async def call(
        self,
        node: str,
        purpose: str,
        messages: Sequence[BaseMessage],
        output: str | None = None,
    ) -> tuple[AIMessage | None, Any]:
        """
        output names the structured output, when it isn't the purpose's own: the record knows a transaction's choice as
        an extraction.
        """
        attempt, wait = 1, None
        while True:
            raw, parsed, outcome, provider = await self.attempt(
                node, purpose, messages, output, attempt, wait
            )
            if outcome == "ok":
                return raw, parsed
            wait = (
                self.retries.after(
                    attempt,
                    self.elapsed(),
                    provider if outcome in WAITED else None,
                    at_once=outcome == "invalid_output",
                )
                if outcome in RETRIED
                else None
            )
            if wait is None:
                raise ModelFailedError(f"the {node} call ended {outcome}")
            await self.retries.sleep(wait.seconds)
            attempt += 1

    async def attempt(
        self,
        node: str,
        purpose: str,
        messages: Sequence[BaseMessage],
        output: str | None,
        attempt: int,
        wait: Wait | None,
    ) -> tuple[AIMessage | None, Any, str, float | None]:
        started = time.perf_counter()
        raw: AIMessage | None = None
        parsed: Any = None
        outcome = "ok"
        provider: float | None = None
        typed = output or purpose
        try:
            answer = await self.factory(typed).ainvoke(list(messages))
            if typed in OUTPUTS:
                raw, parsed = answer["raw"], answer["parsed"]
                if answer.get("parsing_error") is not None or parsed is None:
                    outcome = "invalid_output"
            else:
                raw = answer
                if not answer.text:
                    outcome = "invalid_output"
        except Exception as error:
            outcome = failure(error)
            provider = provider_wait(error)
        counted = usage(raw)
        entry: dict[str, Any] = {
            "node": node,
            "purpose": purpose,
            "provider": self.provider,
            "model_requested": self.requested,
            "model_returned": (
                raw.response_metadata.get("model_name") if raw else None
            ),
            "prompt_version": prompt_version(node),
            "settings": {
                "reasoning": None,
                "max_tokens": MAX_TOKENS,
                "temperature": TEMPERATURE,
            },
            "attempt": attempt,
            **(wait.fields() if wait is not None else {}),
            "outcome": outcome,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "usage": counted,
            "cost_usd": cost(counted),
        }
        if outcome == "ok" and isinstance(parsed, RouterOutput):
            entry["output"] = parsed.model_dump()
        if outcome == "ok" and isinstance(parsed, RequestDetails):
            entry["output"] = {"extracted": parsed.model_dump()}
        if outcome == "ok" and isinstance(parsed, TransactionChoice):
            fitting = ",".join(str(n) for n in parsed.fitting)
            entry["output"] = {
                "extracted": {
                    "fitting": fitting or None,
                    "language": parsed.language,
                    "question": parsed.question,
                }
            }
        await self.record("model_call", **entry)
        return raw, parsed, outcome, provider

    async def route(self, text: str, context: str | None = None) -> RouterOutput:
        """
        context says what the chat's last reply offered, when it offered something a bare message may take up.
        """
        blocks: list[str | dict[Any, Any]] = [{"type": "text", "text": prompt("route")}]
        if context is not None:
            blocks.append({"type": "text", "text": context})
        _, parsed = await self.call(
            "route", "route", [SystemMessage(blocks), HumanMessage(text)]
        )
        routed: RouterOutput = parsed
        return routed

    async def extract(self, text: str, context: str) -> RequestDetails:
        system = SystemMessage(
            [
                {"type": "text", "text": prompt("resolve_card")},
                {"type": "text", "text": context},
            ]
        )
        _, parsed = await self.call(
            "resolve_card", "extract", [system, HumanMessage(text)]
        )
        details: RequestDetails = parsed
        return details

    async def choose(self, text: str, listing: str) -> TransactionChoice:
        system = SystemMessage(
            [
                {"type": "text", "text": prompt("find_transaction")},
                {"type": "text", "text": listing},
            ]
        )
        _, parsed = await self.call(
            "find_transaction", "extract", [system, HumanMessage(text)], "choose"
        )
        chosen: TransactionChoice = parsed
        return chosen

    async def handoff_text(
        self, conversation: str, context: str, done: str | None = None
    ) -> HandoffText:
        """
        The conversation reaches the model as one message of data, never as turns it could continue. done, what the
        chat did about the card, is a block of its own after the context.
        """
        system = SystemMessage(
            [
                {"type": "text", "text": prompt("handoff")},
                {"type": "text", "text": context},
                *([{"type": "text", "text": done}] if done is not None else []),
            ]
        )
        _, parsed = await self.call(
            "handoff", "handoff_text", [system, HumanMessage(conversation)]
        )
        text: HandoffText = parsed
        return text

    async def reply(self, text: str, facts: str, language_name: str) -> str:
        """
        The request's own message is the only turn the model reads, so no figure an earlier reply stated reaches it
        (ADR-0004's amendment of 2026-10-01).
        """
        system = SystemMessage(
            [
                {
                    "type": "text",
                    "text": prompt("reply").format(language=language_name),
                },
                {"type": "text", "text": facts},
            ]
        )
        raw, _ = await self.call("reply", "reply", [system, HumanMessage(text)])
        assert raw is not None
        return raw.text
