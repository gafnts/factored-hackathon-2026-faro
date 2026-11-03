"""
Writes the outside source (ADR-0011, Wording sources): each request sent to OpenAI's Responses API with the guide as
the developer message, its reply held to a JSON schema, and what it wrote checked by families.problems beside ours
before it's kept. A reply that fails is asked again, up to ATTEMPTS times, with the rules it broke in fixed words and
its own phrasings that passed, to keep, beside the places to write anew; a retry sends nothing but the guide, the
request, those words, and the model's own text (SEC-03), never a message of ours. Each kept family and answer names the
model OpenAI returned. The source goes to data/evaluation/sources/outside/, a file per group as
load_source reads it; each attempt's record, with no text, to data/evaluation/outside/<run>/calls.jsonl. What's
already kept is skipped, so a run that stops resumes. The key is read from the env file at the first call, so an
estimate needs none.
"""

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from faro.evaluation import families
from faro.evaluation.outside import (
    ANSWERS,
    PHRASINGS,
    AnswerRequest,
    FamilyRequest,
    answer_bodies,
    family_body,
    guide,
    requests,
)
from faro.model_key import read_key

PROVIDER = "openai"
MODEL = "gpt-6.1-sol"
URL = "https://api.openai.com/v1/responses"
KEY = "OPENAI_API_KEY"
REASONING = "medium"
# USD per million tokens at list price: input, cached input, output.
PRICES = (2.00, 0.10, 10.00)
PRICED_ON = "2026-11-02"
ATTEMPTS = 4
PARALLEL = 8
TIMEOUT = 300.0
RETRIED_STATUS = frozenset({429, 500, 502, 503, 504})
# For the estimate: the guide and a request in tokens, and a reply with its reasoning.
CHARACTERS_PER_TOKEN = 4
OUTPUT_TOKENS = 3000
OUT = Path("data/evaluation/outside")
LANGUAGE_NAMES = {
    "es": "Spanish",
    "pt": "Portuguese",
    "en": "English",
    "fr": "French",
}
# What a retry says for each rule families.problems found broken, keyed by the words of its finding.
# A finding that names a message: a family's ID, language, and place, or an answer's ID and language.
NAMED = re.compile(rf"{families.OUTSIDE}\.[a-z_]+-(\d{{2}})/([a-z]{{2}})(?:/(\d+))?")
BROKEN = {
    "uses slots": "Some messages didn't hold exactly the request's slots, each once.",
    "exactly its kind's slots": "Some answers didn't hold exactly the request's slots, each once.",
    "digit outside a slot": "Some messages held a digit outside a slot.",
    "value outside a slot": "Some answers held a digit or an identifier outside a slot.",
    "stray brace": "Some messages held a brace that isn't a slot's.",
    "shaped like an identifier": "Some messages held something shaped like an identifier.",
    "characters long": "Some messages were longer than three hundred characters, or empty.",
    "reads as": "Some messages read as the other language.",
    "repeats another": "Some messages repeated another.",
    "too alike": "Some messages were near copies of others in the group; make each a different way of saying it.",
    "too close": "Some messages were too close to wording the test already has; choose less common ways to say it.",
}

Request = FamilyRequest | AnswerRequest
Post = Callable[[Mapping[str, Any]], Awaitable[httpx.Response]]
Sleep = Callable[[float], Awaitable[None]]


class OutsideError(ValueError):
    pass


@dataclass(frozen=True)
class Kept:
    request: Request
    bodies: list[dict[str, Any]]


def name(request: Request) -> str:
    return request.family_id if isinstance(request, FamilyRequest) else request.kind


def asked(request: Request) -> dict[str, Any]:
    """
    The request as the model reads it, in the guide's fields.
    """
    if isinstance(request, AnswerRequest):
        return {
            "write": "answers",
            "says": request.says,
            "slots": [f"{{{slot}}}" for slot in request.slots],
            "languages": {lang: LANGUAGE_NAMES[lang] for lang in families.LANGUAGES},
            "answers_per_language": ANSWERS,
        }
    return {
        "write": "a group of messages",
        "labels": list(request.labels),
        "kind": request.kind,
        "says": request.says,
        "extract": dict(request.extract),
        "slots": [f"{{{slot}}}" for slot in request.slots],
        "when": request.when,
        "complaint": request.complaint,
        "languages": {lang: LANGUAGE_NAMES[lang] for lang in request.languages},
        "messages_per_language": PHRASINGS,
    }


def languages(request: Request) -> tuple[str, ...]:
    return (
        families.LANGUAGES if isinstance(request, AnswerRequest) else request.languages
    )


def payload(
    request: Request,
    broken: Sequence[str] = (),
    kept: Mapping[str, Sequence[str | None]] | None = None,
) -> dict[str, Any]:
    """
    kept is a retry's group: the earlier attempt's phrasings that passed, and None in each place to write anew.
    """
    count = ANSWERS if isinstance(request, AnswerRequest) else PHRASINGS
    told = json.dumps(asked(request), ensure_ascii=False, indent=2)
    if broken:
        told += "\n\nAn earlier attempt broke these rules.\n" + "\n".join(
            f"- {rule}" for rule in broken
        )
        told += (
            "\n\nIn the group below, null marks the messages to write anew, each unlike the others; copy the rest "
            "unchanged, in the same places.\n"
            + json.dumps(kept, ensure_ascii=False, indent=2)
            if kept is not None
            else "\n\nWrite the whole group again, keeping to them."
        )
    schema = {
        "type": "object",
        "properties": {
            lang: {
                "type": "array",
                "items": {"type": "string"},
                "minItems": count,
                "maxItems": count,
            }
            for lang in languages(request)
        },
        "required": list(languages(request)),
        "additionalProperties": False,
    }
    return {
        "model": MODEL,
        "input": [
            {"role": "developer", "content": guide()},
            {"role": "user", "content": told},
        ],
        "reasoning": {"effort": REASONING},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "phrasings",
                "strict": True,
                "schema": schema,
            }
        },
        "store": False,
    }


def sendable(sent: Mapping[str, Any]) -> None:
    """
    What goes to OpenAI is the guide, a request, a retry's fixed words, and the model's own phrasings, which hold no
    digit outside a slot: nothing shaped like a record's value.
    """
    text = json.dumps(sent, ensure_ascii=False)
    if families.IDENTIFIER.search(text) or re.search(r"\d{2,}", text):
        raise OutsideError(
            "a request holds something shaped like a record's value (SEC-03)"
        )


def broken_rules(found: Sequence[str]) -> list[str]:
    """
    The rules a reply broke, in the retry's fixed words; a finding with none is ours to fix, not the model's.
    """
    rules = []
    for problem in found:
        rule = next((r for k, r in BROKEN.items() if k in problem), None)
        if rule is None:
            raise OutsideError(
                f"a written group fails a rule no retry fixes: {problem}"
            )
        if rule not in rules:
            rules.append(rule)
    return rules


def failing(found: Sequence[str]) -> dict[str, set[int]] | None:
    """
    The places in each language a retry writes anew, or None when a finding names no message and the whole group goes.
    Of two phrasings too alike, the later is written anew.
    """
    marked: dict[str, set[int]] = {}
    for problem in found:
        named = list(NAMED.finditer(problem))
        if not named:
            return None
        for match in named[-1:] if "too alike" in problem else named:
            number, language, place = match.groups()
            index = int(place) if place is not None else int(number) - 1
            marked.setdefault(language, set()).add(index)
    return marked


def checked(
    request: Request, texts: Mapping[str, Sequence[str]], author: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    The written family or answers, and what families.problems finds in them beside ours, which find nothing.
    """
    ours, our_answers = families.load(), families.load_answers()
    if isinstance(request, AnswerRequest):
        bodies = answer_bodies(request, texts, author)
        written = [families._answer(b, families.OUTSIDE) for b in bodies]
        return bodies, families.problems(ours, [*our_answers, *written])
    body = family_body(request, texts, author)
    family = families._family(request.group, body, families.OUTSIDE)
    return [body], families.problems([*ours, family], our_answers)


def reply_texts(reply: Mapping[str, Any]) -> dict[str, list[str]] | None:
    """
    The reply's JSON, or None when it holds no text: a refusal, or an output cut short.
    """
    if reply.get("status") != "completed":
        return None
    for item in reply.get("output", ()):
        if item.get("type") != "message":
            continue
        for part in item.get("content", ()):
            if part.get("type") == "output_text":
                try:
                    parsed: dict[str, list[str]] = json.loads(part["text"])
                except json.JSONDecodeError:
                    return None
                return parsed
    return None


def usage(reply: Mapping[str, Any]) -> dict[str, int | None]:
    used = reply.get("usage") or {}
    return {
        "input_tokens": used.get("input_tokens"),
        "cached_tokens": (used.get("input_tokens_details") or {}).get("cached_tokens"),
        "output_tokens": used.get("output_tokens"),
        "reasoning_tokens": (used.get("output_tokens_details") or {}).get(
            "reasoning_tokens"
        ),
    }


def cost(counted: Mapping[str, int | None]) -> float | None:
    """
    The input count includes cached tokens, which are priced apart; the output count includes reasoning.
    """
    if counted["input_tokens"] is None or counted["output_tokens"] is None:
        return None
    price_in, price_cached, price_out = PRICES
    cached = counted["cached_tokens"] or 0
    total = (
        (counted["input_tokens"] - cached) * price_in
        + cached * price_cached
        + counted["output_tokens"] * price_out
    )
    return round(total / 1_000_000, 8)


def estimate(pending: Sequence[Request]) -> dict[str, Any]:
    price_in, _, price_out = PRICES
    input_tokens = sum(
        round(len(json.dumps(payload(r), ensure_ascii=False)) / CHARACTERS_PER_TOKEN)
        for r in pending
    )
    output_tokens = OUTPUT_TOKENS * len(pending)
    return {
        "calls": len(pending),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(
            (input_tokens * price_in + output_tokens * price_out) / 1_000_000, 2
        ),
        "priced_on": PRICED_ON,
    }


class Writer:
    def __init__(
        self, post: Post, calls: list[dict[str, Any]], sleep: Sleep = asyncio.sleep
    ) -> None:
        self.post, self.calls, self.sleep = post, calls, sleep

    async def write(self, request: Request) -> Kept | None:
        broken: list[str] = []
        retried: dict[str, list[str | None]] | None = None
        for attempt in range(1, ATTEMPTS + 1):
            sent = payload(request, broken, retried)
            sendable(sent)
            started = time.perf_counter()
            response = await self.post(sent)
            reply: dict[str, Any] = (
                response.json() if response.status_code == 200 else {}
            )
            counted = usage(reply)
            texts = reply_texts(reply) if reply else None
            outcome, found = "ok", list[str]()
            bodies: list[dict[str, Any]] = []
            if response.status_code != 200:
                outcome = f"http_{response.status_code}"
            elif texts is None:
                outcome = "no_text"
            else:
                bodies, found = checked(request, texts, reply["model"])
                if found:
                    outcome = "broke_rules"
            self.calls.append(
                {
                    "request": name(request),
                    "provider": PROVIDER,
                    "model_requested": MODEL,
                    "model_returned": reply.get("model"),
                    "settings": {"reasoning": REASONING, "store": False},
                    "attempt": attempt,
                    "outcome": outcome,
                    "broken": broken_rules(found),
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "usage": counted,
                    "cost_usd": cost(counted),
                    "priced_on": PRICED_ON,
                }
            )
            if outcome == "ok":
                return Kept(request, bodies)
            if outcome.startswith("http_"):
                if response.status_code not in RETRIED_STATUS:
                    raise OutsideError(
                        f"OpenAI answered {response.status_code} for {name(request)}: "
                        f"{response.text[:300]}"
                    )
                wait = response.headers.get("retry-after")
                await self.sleep(float(wait) if wait else float(2**attempt))
            if outcome == "broke_rules" and texts is not None:
                broken, marked = broken_rules(found), failing(found)
                retried = (
                    None
                    if marked is None
                    else {
                        lang: [
                            None if n in marked.get(lang, ()) else text
                            for n, text in enumerate(listed)
                        ]
                        for lang, listed in texts.items()
                    }
                )
        return None


def kept(root: Path = families.SOURCES) -> set[str]:
    """
    What's written already, by request: a family's ID, or a kind of answer.
    """
    written, answers = families.load_source(families.OUTSIDE, root)
    return {f.family_id for f in written} | {a.kind for a in answers}


def pending(root: Path = families.SOURCES) -> list[Request]:
    asked_families, asked_answers = requests()
    every: list[Request] = [*asked_families, *asked_answers]
    done = kept(root)
    return [r for r in every if name(r) not in done]


def keep(found: Kept, root: Path = families.SOURCES) -> None:
    """
    Adds the written family or answers to their group's file, in ID order.
    """
    directory = root / families.OUTSIDE
    directory.mkdir(parents=True, exist_ok=True)
    if isinstance(found.request, AnswerRequest):
        path, key, label, sort = (
            directory / "answers.json",
            "answers",
            None,
            "answer_id",
        )
    else:
        group = found.request.group
        path, key, label, sort = (
            directory / f"{group}.json",
            "families",
            group,
            "family_id",
        )
    held = json.loads(path.read_text(encoding="utf-8"))[key] if path.is_file() else []
    every = sorted([*held, *found.bodies], key=lambda b: str(b[sort]))
    data: dict[str, Any] = {"label": label} if label is not None else {}
    path.write_text(
        json.dumps(data | {key: every}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def write_all(
    todo: Sequence[Request],
    calls: list[dict[str, Any]],
    env_file: Path,
    root: Path = families.SOURCES,
    parallel: int = PARALLEL,
    post: Post | None = None,
    sleep: Sleep = asyncio.sleep,
) -> list[str]:
    """
    Writes every pending request, keeping each as it's done; returns those that failed every attempt.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        if post is None:
            headers = {"Authorization": f"Bearer {read_key(env_file, KEY)}"}

            async def post(body: Mapping[str, Any]) -> httpx.Response:
                return await client.post(URL, json=body, headers=headers)

        writer = Writer(post, calls, sleep)
        gate = asyncio.Semaphore(parallel)

        async def one(request: Request) -> Kept | None:
            async with gate:
                return await writer.write(request)

        for done in asyncio.as_completed([one(r) for r in todo]):
            found = await done
            if found is not None:
                keep(found, root)
    written = kept(root)
    return [name(r) for r in todo if name(r) not in written]


def write_calls(out: Path, calls: Sequence[Mapping[str, Any]]) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / "calls.jsonl"
    path.write_text(
        "".join(
            json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in calls
        ),
        encoding="utf-8",
    )
    return path


def source_problems(root: Path = families.SOURCES) -> list[str]:
    written, answers = families.load_source(families.OUTSIDE, root)
    return families.problems(
        [*families.load(), *written], [*families.load_answers(), *answers]
    )
