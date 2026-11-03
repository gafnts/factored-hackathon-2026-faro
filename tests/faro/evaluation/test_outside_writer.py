"""
The outside source's writer against a fake OpenAI: every request written and kept as load_source reads it, passing the
families' checks beside ours; a reply that breaks a rule asked again in fixed words; a failure worth repeating waited
out and any other refused; what's kept skipped on a second run; and nothing shaped like a record's value sent (SEC-03).
"""

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from faro.evaluation import families
from faro.evaluation.outside import writer

# Phrasings unlike each other and unlike ours, each with a word only its language uses.
WRITTEN = {
    "es": (
        "necesito una mano con esto, tarjeta",
        "buenas tardes, consulto por la tarjeta",
        "quisiera que me orienten, tarjeta",
        "le escribo desde Medellín por la tarjeta",
        "disculpe la hora, es por la tarjeta",
        "otra vez yo, sigo con la tarjeta",
    ),
    "pt": (
        "preciso de uma força nisso, cartão",
        "boa tarde, é sobre o cartão",
        "queria uma orientação, cartão",
        "escrevo de Recife pelo cartão",
        "desculpa o horário, é o cartão",
        "sou eu de novo, ainda o cartão",
    ),
    "en": (
        "hello, quick question here",
        "good evening to whoever reads",
        "sorry to bother, one thing",
        "hi, writing from Toronto",
        "may I ask something today",
        "back again with my doubt",
    ),
    "fr": (
        "bonjour, petite question ici",
        "bonsoir à qui me lira",
        "pardon du dérangement, une chose",
        "salut, je vous écris de Lyon",
        "puis-je demander quelque chose",
        "me revoilà avec mon doute",
    ),
}


def reply(
    texts: Mapping[str, list[str]], model: str = "gpt-6.1-sol-2026-10-20"
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "model": model,
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(texts)}],
                },
            ],
            "usage": {
                "input_tokens": 1000,
                "input_tokens_details": {"cached_tokens": 400},
                "output_tokens": 500,
                "output_tokens_details": {"reasoning_tokens": 300},
            },
        },
    )


def request_of(body: Mapping[str, Any]) -> dict[str, Any]:
    told = body["input"][1]["content"]
    asked: dict[str, Any] = json.loads(told.split("\n\nAn earlier attempt")[0])
    return asked


def good(body: Mapping[str, Any]) -> dict[str, list[str]]:
    asked = request_of(body)
    count = asked.get("messages_per_language") or asked["answers_per_language"]
    slots = " ".join(asked["slots"])
    return {
        lang: [f"{text} {slots}".strip() for text in WRITTEN[lang][:count]]
        for lang in asked["languages"]
    }


class Fake:
    def __init__(self, *replies: Any) -> None:
        self.sent: list[Mapping[str, Any]] = []
        self.replies = list(replies)

    async def __call__(self, body: Mapping[str, Any]) -> httpx.Response:
        writer.sendable(body)
        self.sent.append(body)
        found = self.replies.pop(0) if self.replies else None
        if isinstance(found, httpx.Response):
            return found
        return reply(found(body) if callable(found) else good(body))


async def no_wait(seconds: float) -> None:
    return None


def run(
    todo: list[writer.Request], root: Path, fake: Fake
) -> tuple[list[str], list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    failed = asyncio.run(
        writer.write_all(
            todo, calls, Path("absent.env"), root, post=fake, sleep=no_wait
        )
    )
    return failed, calls


def test_every_request_is_written_and_kept_as_load_source_reads_it(
    tmp_path: Path,
) -> None:
    todo = writer.pending(tmp_path)
    failed, calls = run(todo, tmp_path, Fake())

    written, answers = families.load_source(families.OUTSIDE, tmp_path)
    assert failed == []
    assert len(written) == 70 and len(answers) == 55
    assert writer.source_problems(tmp_path) == []
    assert {m.author for f in written for m in f.messages} == {"gpt-6.1-sol-2026-10-20"}
    assert {a.author for a in answers} == {"gpt-6.1-sol-2026-10-20"}
    assert all(c["outcome"] == "ok" and c["cost_usd"] == 0.00624 for c in calls)
    assert writer.pending(tmp_path) == []


def test_a_reply_that_breaks_a_rule_is_asked_again_keeping_what_passed(
    tmp_path: Path,
) -> None:
    request = writer.pending(tmp_path)[0]

    def with_a_digit(body: Mapping[str, Any]) -> dict[str, list[str]]:
        texts = good(body)
        texts["es"][2] = "tengo 2 tarjetas y una perdida"
        return texts

    fake = Fake(with_a_digit, None)
    failed, calls = run([request], tmp_path, fake)

    assert failed == []
    assert [c["outcome"] for c in calls] == ["broke_rules", "ok"]
    retry = fake.sent[1]["input"][1]["content"]
    kept = json.loads(retry[retry.index("{", retry.index("null marks")) :])
    assert "Some messages held a digit outside a slot." in retry
    assert "tengo" not in retry
    assert kept["es"][2] is None
    assert kept["es"][:2] == list(WRITTEN["es"][:2])
    assert None not in kept["pt"]


@pytest.mark.parametrize(
    ("found", "marked"),
    [
        (
            ["held-out outside.none-01/es/3 is too close to development none-01/es/0"],
            {"es": {3}},
        ),
        (
            [
                "outside.none-01/pt/1 and outside.none-01/pt/4 are too alike to count as two phrasings"
            ],
            {"pt": {4}},
        ),
        (
            ["held-out outside.aside-02/es is too close to development aside-01/es"],
            {"es": {1}},
        ),
        (["outside.none-01 has 5 es messages"], None),
    ],
)
def test_a_retry_writes_anew_only_the_places_a_finding_names(
    found: list[str], marked: dict[str, set[int]] | None
) -> None:
    assert writer.failing(found) == marked


def test_a_request_that_fails_every_attempt_is_left_out(tmp_path: Path) -> None:
    request = writer.pending(tmp_path)[0]

    def alike(body: Mapping[str, Any]) -> dict[str, list[str]]:
        texts = good(body)
        return {lang: [listed[0]] * len(listed) for lang, listed in texts.items()}

    failed, calls = run([request], tmp_path, Fake(*[alike] * writer.ATTEMPTS))

    assert failed == [writer.name(request)]
    assert len(calls) == writer.ATTEMPTS
    assert writer.kept(tmp_path) == set()


def test_a_failure_worth_repeating_is_waited_out_and_others_refused(
    tmp_path: Path,
) -> None:
    request = writer.pending(tmp_path)[0]
    busy = httpx.Response(429, headers={"retry-after": "1"}, json={})

    failed, calls = run([request], tmp_path, Fake(busy, None))
    assert failed == []
    assert [c["outcome"] for c in calls] == ["http_429", "ok"]

    refused = httpx.Response(401, json={"error": {"message": "bad key"}})
    with pytest.raises(writer.OutsideError, match="401"):
        run(writer.pending(tmp_path)[:1], tmp_path, Fake(refused))


def test_nothing_shaped_like_a_record_value_is_sent() -> None:
    request = writer.pending(Path("absent"))[0]
    writer.sendable(writer.payload(request, ["Some messages repeated another."]))

    for value in ("ending 4821", "CLI-00012", "12.50"):
        with pytest.raises(writer.OutsideError):
            writer.sendable({"input": f"the card {value}"})


def test_the_estimate_prices_every_pending_request_without_a_key() -> None:
    priced = writer.estimate(writer.pending(Path("absent")))

    assert priced["calls"] == 81
    assert 0 < priced["cost_usd"] < 10
