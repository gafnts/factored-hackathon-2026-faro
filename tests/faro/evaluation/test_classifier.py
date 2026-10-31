"""
The router comparison's LLM candidates send the deployed router's prompt through the agent's own chain, each model
with the settings it accepts, try a call again only on a failure worth repeating, record every attempt with what the
manifest needs, and read no key until they call (ADR-0005, Baselines: the router, as amended on 2026-10-02; DML-07).
No test calls a model.
"""

import asyncio
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableLambda

from faro.agent import models
from faro.agent.models import RouterOutput
from faro.evaluation import __main__ as cli
from faro.evaluation import classifier, model_commands
from faro.evaluation.router import Candidate
from faro.model_key import ModelKeyError

USAGE = {
    "input_tokens": 1200,
    "output_tokens": 40,
    "total_tokens": 1240,
    "input_token_details": {"cache_read": 200, "cache_creation": 0},
}


def env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=sk-test\n", encoding="utf-8")
    return path


def answered(model: str, routed: RouterOutput | None) -> dict[str, Any]:
    raw = AIMessage(
        content="{}", usage_metadata=USAGE, response_metadata={"model_name": model}
    )
    return {"raw": raw, "parsed": routed, "parsing_error": None}


ROUTED = RouterOutput(
    language="es",
    requests=["card_status", "block_card"],
    has_request=True,
    complaint=False,
)


class Scripted:
    """
    The chain, answering from a script: each entry an answer or an error to raise.
    """

    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.seen: list[list[BaseMessage]] = []
        self.made: list[tuple[str, str]] = []

    def make(self, model: str, key: str) -> RunnableLambda[Any, Any]:
        self.made.append((model, key))

        async def invoke(messages: list[BaseMessage]) -> Any:
            self.seen.append(messages)
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

        return RunnableLambda(invoke)


def status_error(kind: Any, status: int, **headers: str) -> Exception:
    request = httpx2.Request("POST", "https://api.anthropic.example")
    error: Exception = kind(
        "failed",
        response=httpx2.Response(status, request=request, headers=headers),
        body=None,
    )
    return error


def made(
    tmp_path: Path, model: str, script: list[Any]
) -> tuple[Candidate, Scripted, list[dict[str, Any]], list[float]]:
    scripted = Scripted(script)
    calls: list[dict[str, Any]] = []
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    found = classifier.candidate(
        "c", model, env_file(tmp_path), calls, scripted.make, sleep
    )
    return found, scripted, calls, slept


def test_a_reading_is_the_router_s_own_call_recorded_and_priced(tmp_path: Path) -> None:
    found, scripted, calls, _ = made(
        tmp_path, models.MODEL, [answered(models.MODEL, ROUTED)]
    )

    reading = asyncio.run(found.read("¿Mi tarjeta? Y bloquéela."))

    assert reading.labels == ("card_status", "block_card") and reading.has_request
    assert not reading.failed
    assert scripted.made == [(models.MODEL, "sk-test")]
    system, human = scripted.seen[0]
    assert system.content == [{"type": "text", "text": models.prompt("route")}]
    assert human.content == "¿Mi tarjeta? Y bloquéela."
    (call,) = calls
    assert call["model_requested"] == call["model_returned"] == models.MODEL
    assert call["prompt_version"] == models.prompt_version("route")
    assert call["settings"] == {
        "max_tokens": 2048,
        "temperature": 0.0,
        "thinking": None,
        "batch": False,
    }
    assert call["outcome"] == "ok" and call["attempt"] == 1
    assert call["priced_on"] == classifier.PRICED_ON[models.MODEL]
    expected = (1000 * 1.00 + 40 * 5.00 + 200 * 0.10) / 1_000_000
    assert call["cost_usd"] == pytest.approx(expected)
    assert reading.cost_usd == pytest.approx(expected)


def test_each_model_is_built_with_the_settings_it_accepts() -> None:
    haiku = classifier.chat_settings(models.MODEL, "k")
    sonnet = classifier.chat_settings(classifier.SONNET, "k")

    assert haiku["temperature"] == models.TEMPERATURE and "thinking" not in haiku
    assert sonnet["temperature"] is None and sonnet["thinking"] == {
        "type": "between_tools"
    }
    for settings in (haiku, sonnet):
        assert settings["max_retries"] == 0 and settings["disable_streaming"] is True
        assert settings["max_tokens"] == models.MAX_TOKENS
    assert classifier.chain(classifier.SONNET, "k") is not None


def test_a_failure_worth_repeating_is_tried_again_and_each_attempt_recorded(
    tmp_path: Path,
) -> None:
    limited = status_error(anthropic.RateLimitError, 429, **{"retry-after": "3"})
    script = [
        limited,
        answered(classifier.SONNET, None),
        answered(classifier.SONNET, ROUTED),
    ]
    found, _, calls, slept = made(tmp_path, classifier.SONNET, script)

    reading = asyncio.run(found.read("¿Mi tarjeta?"))

    assert reading.has_request and not reading.failed
    assert [c["outcome"] for c in calls] == ["rate_limited", "invalid_output", "ok"]
    assert [c["attempt"] for c in calls] == [1, 2, 3]
    assert slept == [3.0]
    assert calls[0]["model_returned"] is None and calls[0]["cost_usd"] is None
    assert calls[1]["settings"]["thinking"] == "between_tools"
    assert reading.cost_usd == pytest.approx(
        calls[1]["cost_usd"] + calls[2]["cost_usd"]
    )


def test_a_call_that_keeps_failing_reads_as_no_request_and_says_it_failed(
    tmp_path: Path,
) -> None:
    script = [answered(models.MODEL, None)] * classifier.ATTEMPTS
    found, _, calls, _ = made(tmp_path, models.MODEL, list(script))

    reading = asyncio.run(found.read("hola"))

    assert reading.failed and not reading.has_request and reading.labels == ()
    assert len(calls) == classifier.ATTEMPTS


def test_a_refused_request_is_not_repeated(tmp_path: Path) -> None:
    found, _, calls, slept = made(
        tmp_path, models.MODEL, [status_error(anthropic.BadRequestError, 400)]
    )

    reading = asyncio.run(found.read("hola"))

    assert reading.failed and [c["outcome"] for c in calls] == ["error"] and slept == []


def test_no_key_is_read_until_a_call(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    found = classifier.haiku(tmp_path / "missing.env", calls)

    priced = found.estimate(["¿Mi tarjeta?"] * 10) if found.estimate else {}

    assert priced["calls"] == 10 and priced["cost_usd"] > 0
    with pytest.raises(ModelKeyError):
        asyncio.run(found.read("hola"))
    assert calls == []
    bigger = classifier.sonnet(tmp_path / "missing.env", calls)
    assert (
        bigger.estimate is not None
        and bigger.estimate(["¿Mi tarjeta?"] * 10)["cost_usd"] > priced["cost_usd"]
    )
    assert (found.model, bigger.model) == (models.MODEL, "claude-sonnet-5-5")
    assert found.settings["prompt_version"] == models.prompt_version("route")


def test_both_candidates_are_registered_and_priced_without_a_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert {"haiku", "sonnet"} <= set(model_commands.CANDIDATES)

    code = cli.main(
        [
            "router",
            "--candidate",
            "haiku",
            "--candidate",
            "sonnet",
            "--estimate",
            "--env-file",
            str(tmp_path / "none"),
        ]
    )

    printed = capsys.readouterr().out.splitlines()
    assert code == 0
    assert [line.split(":")[0] for line in printed] == ["haiku", "sonnet"]
