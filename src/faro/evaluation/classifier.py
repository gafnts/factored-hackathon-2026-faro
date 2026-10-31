"""
The router comparison's LLM candidates (ADR-0005, Baselines: the router, as amended on 2026-10-02; DML-07): one
classifier prompt on Claude Haiku 4.5 and Claude Sonnet 5.5. The prompt is the deployed router's own (the agent's
route prompt, which the split's guards keep free of held-out messages) and so is the call: the chain the agent builds,
with structured output into RouterOutput, so the Haiku candidate is the router the chat deploys and the Sonnet one is
that router on a larger model of the family. Each call goes to Anthropic's API directly, not batched, since latency
per call is measured.

The settings follow each model: Haiku 4.5 as the agent calls it, at temperature 0 with no thinking; Sonnet 5.5 refuses
a temperature other than its default and turns thinking off only as between_tools, so it runs at its default sampling
with thinking off. The client's own retries are off, and a call is tried again up to ATTEMPTS times on a failure worth
repeating (decision 18's kinds), each attempt recorded with the model requested and returned, the prompt's version,
the settings, tokens, latency, and cost at list price with the date of the price. The key is read from the env file
at the first call, so an estimate needs none.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from faro.agent import models
from faro.agent.models import RouterOutput
from faro.evaluation.router import Candidate, Reading
from faro.model_key import read_key

SONNET = "claude-sonnet-5-5"
# USD per million tokens at list price: input, output, cache write, cache read. Haiku's is the agent's.
PRICES = {**models.PRICES, SONNET: (2.00, 10.00, 2.50, 0.20)}
PRICED_ON = {models.MODEL: "2026-09-29", SONNET: "2026-09-25"}
SETTINGS: dict[str, dict[str, Any]] = {
    models.MODEL: {
        "max_tokens": models.MAX_TOKENS,
        "temperature": models.TEMPERATURE,
        "thinking": None,
    },
    SONNET: {
        "max_tokens": models.MAX_TOKENS,
        "temperature": None,
        "thinking": {"type": "between_tools"},
    },
}
ATTEMPTS = 3
# For an estimate before any call: characters per token, what structured output adds to the prompt, and the reading's
# length, the last two set high on purpose.
CHARACTERS_PER_TOKEN = 3.0
SCHEMA_TOKENS = 400
OUTPUT_TOKENS = 60

Make = Callable[[str, str], Runnable[Any, Any]]
Sleep = Callable[[float], Awaitable[None]]


def chat_settings(model: str, key: str) -> dict[str, Any]:
    """
    What the chat model is built with: the agent's settings, and the model's own where it refuses the agent's.
    """
    settings = SETTINGS[model]
    found: dict[str, Any] = {
        "api_key": key,
        "max_tokens": settings["max_tokens"],
        "temperature": settings["temperature"],
        "timeout": models.TIMEOUT_S,
        "max_retries": 0,
        "disable_streaming": True,
    }
    if settings["thinking"] is not None:
        found["thinking"] = settings["thinking"]
    return found


def chain(model: str, key: str) -> Runnable[Any, Any]:
    chat = init_chat_model(f"{models.PROVIDER}:{model}", **chat_settings(model, key))
    runnable: Runnable[Any, Any] = chat.with_structured_output(
        RouterOutput, method="json_schema", include_raw=True
    )
    return runnable


def cost(model: str, counted: Mapping[str, int | None]) -> float | None:
    """
    The input count includes cached tokens, which are priced apart.
    """
    if counted["input_tokens"] is None or counted["output_tokens"] is None:
        return None
    price_in, price_out, price_write, price_read = PRICES[model]
    read = counted["cache_read_tokens"] or 0
    written = counted["cache_write_tokens"] or 0
    total = (
        (counted["input_tokens"] - read - written) * price_in
        + counted["output_tokens"] * price_out
        + written * price_write
        + read * price_read
    )
    return round(total / 1_000_000, 8)


def recorded_settings(model: str) -> dict[str, Any]:
    settings = SETTINGS[model]
    thinking = settings["thinking"]
    return {
        **settings,
        "thinking": thinking["type"] if thinking else None,
        "batch": False,
    }


class Classifier:
    def __init__(
        self,
        name: str,
        model: str,
        env_file: Path,
        calls: list[dict[str, Any]],
        make: Make = chain,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.name, self.model, self.env_file = name, model, env_file
        self.calls, self.make, self.sleep = calls, make, sleep
        self.runnable: Runnable[Any, Any] | None = None

    def ready(self) -> Runnable[Any, Any]:
        if self.runnable is None:
            self.runnable = self.make(self.model, read_key(self.env_file))
        return self.runnable

    async def read(self, text: str) -> Reading:
        runnable = self.ready()
        messages = [
            SystemMessage([{"type": "text", "text": models.prompt("route")}]),
            HumanMessage(text),
        ]
        spent = 0.0
        for attempt in range(1, ATTEMPTS + 1):
            started = time.perf_counter()
            raw: AIMessage | None = None
            parsed: Any = None
            outcome, wait = "ok", None
            try:
                answer = await runnable.ainvoke(messages)
                raw, parsed = answer["raw"], answer["parsed"]
                if answer.get("parsing_error") is not None or parsed is None:
                    outcome = "invalid_output"
            except Exception as error:
                outcome = models.failure(error)
                wait = models.provider_wait(error)
            counted = models.usage(raw)
            priced = cost(self.model, counted)
            spent += priced or 0.0
            self.calls.append(
                {
                    "candidate": self.name,
                    "provider": models.PROVIDER,
                    "model_requested": self.model,
                    "model_returned": raw.response_metadata.get("model_name")
                    if raw
                    else None,
                    "prompt_version": models.prompt_version("route"),
                    "settings": recorded_settings(self.model),
                    "attempt": attempt,
                    "outcome": outcome,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "usage": counted,
                    "cost_usd": priced,
                    "priced_on": PRICED_ON[self.model],
                }
            )
            if outcome == "ok":
                routed: RouterOutput = parsed
                return Reading(
                    tuple(routed.requests), routed.has_request, cost_usd=round(spent, 8)
                )
            if outcome not in models.RETRIED or attempt == ATTEMPTS:
                break
            # As the agent does, an invalid output is asked again at once.
            if outcome != "invalid_output":
                await self.sleep(
                    wait if wait is not None else float(2 ** (attempt - 1))
                )
        return Reading((), False, cost_usd=round(spent, 8), failed=True)

    def estimate(self, texts: Sequence[str]) -> dict[str, Any]:
        price_in, price_out, _, _ = PRICES[self.model]
        prompt = len(models.prompt("route"))
        input_tokens = sum(
            round((prompt + len(t)) / CHARACTERS_PER_TOKEN) + SCHEMA_TOKENS
            for t in texts
        )
        output_tokens = OUTPUT_TOKENS * len(texts)
        return {
            "calls": len(texts),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(
                (input_tokens * price_in + output_tokens * price_out) / 1_000_000, 4
            ),
            "priced_on": PRICED_ON[self.model],
        }


def candidate(
    name: str,
    model: str,
    env_file: Path,
    calls: list[dict[str, Any]],
    make: Make = chain,
    sleep: Sleep = asyncio.sleep,
) -> Candidate:
    reader = Classifier(name, model, env_file, calls, make, sleep)
    return Candidate(
        name,
        reader.read,
        model=model,
        settings={
            **recorded_settings(model),
            "provider": models.PROVIDER,
            "prompt_version": models.prompt_version("route"),
            "attempts": ATTEMPTS,
        },
        estimate=reader.estimate,
    )


def haiku(env_file: Path, calls: list[dict[str, Any]]) -> Candidate:
    return candidate("haiku", models.MODEL, env_file, calls)


def sonnet(env_file: Path, calls: list[dict[str, Any]]) -> Candidate:
    return candidate("sonnet", SONNET, env_file, calls)
