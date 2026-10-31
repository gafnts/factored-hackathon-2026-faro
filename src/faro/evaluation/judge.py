"""
The judge (ADR-0005, Grading, as amended on 2026-10-02; EVL-10): Claude Opus 5.5 answers every rubric question a
reply's turn calls for in one structured call, through Anthropic's Message Batches API at half the list price, since
it is outside the system under test. Haiku 4.5 is never the judge: it wrote the replies.

An item is one reply with what the judge reads beside it: the turns before it, what the customer sent, the turn's
decision entries, and, when the turn filed a handoff, the case a person will read. Items come from a run's stored
evidence, in process or end to end, so judging costs no play. Code decides which questions apply (rubric.Facts) and
which answers pass, so the judge never sees what we expect; a judgment in a confidence level the rubric grades by hand,
or an item the judge never answered, goes to a person.

A request that fails is sent again in the next batch, up to ATTEMPTS, and every attempt is recorded with what the run
manifest asks for: the model requested and returned, the prompt's hash, the rubric's version, tokens, and cost at the
batch's list price with the date of the price. The API takes no sampling seed for this model, so none is recorded.
Items and judgments hold the reply's values, so they stay under data/evaluation/judge/, never committed (SEC-03).
"""

import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol

import anthropic
from jsonschema import Draft202012Validator

from faro.evaluation import grader
from faro.evaluation.rubric import Facts, Question, Rubric

MODEL = "claude-opus-5-5"
PROVIDER = "anthropic"
MAX_TOKENS = 16000
# Opus 5.5 always thinks; effort is the only control, and its default is set here so a change of default can't move it.
EFFORT = "medium"
# USD per million tokens at list price on 2026-09-25: input, output, cache write, cache read. A batch pays half.
PRICES = {MODEL: (4.00, 20.00, 5.00, 0.20)}
PRICED_ON = "2026-09-25"
BATCH_SHARE = 0.5
ATTEMPTS = 3
POLL_S = 60.0
# Retried in the next batch; an invalid request would only fail again.
RETRIED = ("server_error", "expired", "canceled", "missing", "invalid_output")
OUT = Path("data/evaluation/judge")
PRESSED = {
    "confirm": "[pressed the confirm button]",
    "cancel": "[pressed the cancel button]",
    "accept": "[accepted the handoff with its button]",
    "decline": "[declined the handoff with its button]",
}
# For an estimate before any call: characters per token in these prompts, and output tokens per question and per
# reply for the thinking Opus does at EFFORT, both set high on purpose.
CHARACTERS_PER_TOKEN = 3.0
OUTPUT_PER_QUESTION = 120
THINKING_PER_REPLY = 1500


@dataclass(frozen=True)
class Item:
    """
    language is the one the turn decided to reply in (POL-50). seeded_for names the question a seeded reply was
    edited to fail (blind.py), and source the reply it was edited from.
    """

    item_id: str
    case_id: str
    turn: int
    language: str
    earlier: tuple[tuple[str, str], ...]
    sent: str
    decisions: tuple[Mapping[str, Any], ...]
    reply: str
    handoff: Mapping[str, Any] | None
    facts: Facts
    seeded_for: str | None = None
    source: str | None = None

    def to_json(self) -> dict[str, Any]:
        found = asdict(self)
        found["facts"] = {
            "rules": sorted(self.facts.rules),
            "handoff_filed": self.facts.handoff_filed,
            "confirmation_ended": self.facts.confirmation_ended,
        }
        return found

    @classmethod
    def from_json(cls, body: Mapping[str, Any]) -> "Item":
        facts = body["facts"]
        return cls(
            item_id=body["item_id"],
            case_id=body["case_id"],
            turn=body["turn"],
            language=body["language"],
            earlier=tuple((a, b) for a, b in body["earlier"]),
            sent=body["sent"],
            decisions=tuple(body["decisions"]),
            reply=body["reply"],
            handoff=body["handoff"],
            facts=Facts(
                rules=frozenset(facts["rules"]),
                handoff_filed=facts["handoff_filed"],
                confirmation_ended=facts["confirmation_ended"],
            ),
            seeded_for=body.get("seeded_for"),
            source=body.get("source"),
        )


def sent(opened: Mapping[str, Any]) -> str:
    given = opened["input"]
    if given["kind"] == "message":
        return str(given["text"])
    resume = given.get("resume") or {}
    if resume.get("kind") == "message":
        return f"[typed while a button was pending] {resume.get('text', '')}"
    return PRESSED.get(resume.get("kind", ""), f"[{given['kind']}]")


def decision(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "label": entry["request_label"],
        "outcome": entry["outcome_class"],
        "awaiting": entry["awaiting"],
        "queued": list(entry["pending_labels"]),
        "rules": list(entry["rules"]),
    }


def handoff_case(
    case: Mapping[str, Any], said: Mapping[str, str]
) -> dict[str, Any] | None:
    """
    What the person who picks the case up reads, without the records' identifiers, beside what the customer sent in
    the turns the case cites, so the judge can tell whether the free text states the request.
    """
    payload = case.get("payload")
    if not payload:
        return None
    return {
        "reason_code": payload["reason_code"],
        "queue": payload["queue"],
        "priority": payload["priority"],
        "request": payload["request"],
        "customer_statements": payload.get("customer_statements", []),
        "unresolved_questions": payload.get("unresolved_questions", []),
        "verified_facts": [
            {k: f[k] for k in ("subject", "field", "value")}
            for f in payload.get("verified_facts", [])
        ],
        "actions": [
            {k: a.get(k) for k in ("action", "outcome", "reason")}
            for a in payload.get("actions", [])
        ],
        "customer_sent": [
            said[t] for t in (case.get("record") or {}).get("turns", []) if t in said
        ],
    }


def items(evidence: Mapping[str, Any]) -> list[Item]:
    """
    One item per reply the record holds, in the order the turns ran.
    """
    turns = grader.record_turns(evidence["record"])
    filed = {
        c["handoff_id"]: c
        for c in evidence.get("cases", [])
        if c.get("kind") == "case" and c.get("status") == "filed"
    }
    # A case cites a turn by its entry keys' common prefix.
    said = {
        e["entry_key"].rpartition("#")[0]: sent(e)
        for entries in turns
        for e in entries
        if e["kind"] == "turn_opened"
    }
    found: list[Item] = []
    earlier: list[tuple[str, str]] = []
    for n, entries in enumerate(turns):
        opened = next(e for e in entries if e["kind"] == "turn_opened")
        message = sent(opened)
        decisions = [e for e in entries if e["kind"] == "decision"]
        handoffs = [
            e for e in entries if e["kind"] == "handoff" and e["status"] == "filed"
        ]
        case = next(
            (filed[h["handoff_id"]] for h in handoffs if h["handoff_id"] in filed), None
        )
        facts = Facts(
            rules=frozenset(r for d in decisions for r in d["rules"]),
            handoff_filed=bool(handoffs),
            confirmation_ended=any(
                e["kind"] == "confirmation" and e["to"] in ("cancelled", "lapsed")
                for e in entries
            ),
        )
        for reply in (e for e in entries if e["kind"] == "reply"):
            found.append(
                Item(
                    item_id=f"{evidence['case_id']}-t{n + 1:02d}",
                    case_id=evidence["case_id"],
                    turn=n + 1,
                    language=reply["language"],
                    earlier=tuple(earlier),
                    sent=message,
                    decisions=tuple(decision(d) for d in decisions),
                    reply=reply["text"],
                    handoff=handoff_case(case, said) if case is not None else None,
                    facts=facts,
                )
            )
            earlier.append((message, reply["text"]))
    return found


def evidences(run: Path) -> Iterator[dict[str, Any]]:
    """
    A run's stored evidence: one line per case in process, one file per case end to end.
    """
    played = run / "evidence.jsonl"
    if played.is_file():
        with played.open(encoding="utf-8") as kept:
            for line in kept:
                yield json.loads(line)
        return
    for path in sorted((run / "cases").glob("*.json")):
        yield json.loads(path.read_text(encoding="utf-8"))["evidence"]


def run_items(run: Path) -> list[Item]:
    return [i for e in evidences(run) if not e.get("error") for i in items(e)]


def chosen(found: Sequence[Item], rubric: Rubric, limit: int) -> list[Item]:
    """
    A smoke test's items: the first that each question applies to, then the rest in order, up to limit.
    """
    first = []
    for q in rubric.questions:
        hit = next((i for i in found if q.applies(i.facts) and i not in first), None)
        if hit is not None:
            first.append(hit)
    rest = [i for i in found if i not in first]
    return (first + rest)[:limit]


def read_items(path: Path) -> list[Item]:
    with path.open(encoding="utf-8") as kept:
        return [Item.from_json(json.loads(line)) for line in kept if line.strip()]


def write_items(path: Path, found: Iterable[Item]) -> str:
    body = "".join(
        json.dumps(i.to_json(), ensure_ascii=False, sort_keys=True) + "\n"
        for i in found
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return hashlib.sha256(body.encode()).hexdigest()


def system_prompt(rubric: Rubric) -> str:
    template = (
        files("faro.evaluation")
        .joinpath("prompts/judge.md")
        .read_text(encoding="utf-8")
    )
    return template.replace("{questions}", questions_text(rubric.questions))


def prompt_version(rubric: Rubric) -> str:
    return hashlib.sha256(system_prompt(rubric).encode()).hexdigest()[:16]


def questions_text(questions: Iterable[Question]) -> str:
    kinds = {"choice": "a choice", "yes_no": "yes or no", "score": "a score"}
    blocks = []
    for q in questions:
        lines = [f"### {q.id} ({kinds[q.type]})", "", q.text]
        if q.options:
            lines += ["", *(f"- `{k}`: {v}" for k, v in q.options.items())]
        if q.type == "score":
            lines += [
                "",
                f"Answer with a whole number from {q.scale[0]} to {q.scale[1]}.",
            ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def rendered(item: Item, asked: Sequence[Question]) -> str:
    """
    The user message: the item's parts, each inside its tag, as the prompt describes them.
    """
    parts = []
    if item.earlier:
        lines = [
            line
            for customer, chat in item.earlier
            for line in (f"Customer: {customer}", f"Chat: {chat}")
        ]
        parts.append(tagged("earlier", "\n".join(lines)))
    parts.append(tagged("sent", item.sent))
    rows = [
        f"{n}. label: {d['label'] or 'none (no card request)'}; outcome: {d['outcome']}; "
        f"waits for: {d['awaiting']}; still queued: {', '.join(d['queued']) or 'none'}; "
        f"rules: {', '.join(d['rules']) or 'none'}"
        for n, d in enumerate(item.decisions, 1)
    ]
    parts.append(tagged("decision", "\n".join(rows) or "none recorded"))
    parts.append(tagged("reply", item.reply))
    if item.handoff is not None:
        parts.append(
            tagged("handoff", json.dumps(item.handoff, ensure_ascii=False, indent=1))
        )
    parts.append(tagged("questions", ", ".join(q.id for q in asked)))
    return "\n\n".join(parts)


def tagged(name: str, body: str) -> str:
    return f"<{name}>\n{body}\n</{name}>"


def request(item: Item, rubric: Rubric, system: str) -> dict[str, Any]:
    asked = rubric.applicable(item.facts)
    return {
        "custom_id": item.item_id,
        "params": {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "system": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": rendered(item, asked)}],
            "output_config": {
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": rubric.schema(asked)},
            },
        },
    }


class Batches(Protocol):
    """
    The Message Batches API as the judge uses it, so tests can stand in for it. A result is the API's, as a dict.
    """

    def create(self, requests: list[dict[str, Any]]) -> str: ...

    def status(self, batch_id: str) -> str: ...

    def results(self, batch_id: str) -> Iterable[tuple[str, dict[str, Any]]]: ...


class AnthropicBatches:
    def __init__(self, client: anthropic.Anthropic) -> None:
        self.client = client

    def create(self, requests: list[dict[str, Any]]) -> str:
        batch = self.client.messages.batches.create(requests=requests)  # type: ignore[arg-type]
        return batch.id

    def status(self, batch_id: str) -> str:
        return self.client.messages.batches.retrieve(batch_id).processing_status

    def results(self, batch_id: str) -> Iterator[tuple[str, dict[str, Any]]]:
        for found in self.client.messages.batches.results(batch_id):
            yield found.custom_id, found.result.model_dump(mode="json")


def usage(message: Mapping[str, Any] | None) -> dict[str, int | None]:
    counted = (message or {}).get("usage") or {}
    return {
        "input_tokens": counted.get("input_tokens"),
        "output_tokens": counted.get("output_tokens"),
        "cache_read_tokens": counted.get("cache_read_input_tokens"),
        "cache_write_tokens": counted.get("cache_creation_input_tokens"),
    }


def cost(counted: Mapping[str, int | None]) -> float | None:
    """
    The API counts cached tokens apart from its input count, and a batch pays BATCH_SHARE of each price.
    """
    if counted["input_tokens"] is None or counted["output_tokens"] is None:
        return None
    price_in, price_out, price_write, price_read = PRICES[MODEL]
    total = (
        counted["input_tokens"] * price_in
        + counted["output_tokens"] * price_out
        + (counted["cache_write_tokens"] or 0) * price_write
        + (counted["cache_read_tokens"] or 0) * price_read
    )
    return round(total * BATCH_SHARE / 1_000_000, 8)


def read_result(
    result: Mapping[str, Any] | None, validator: Draft202012Validator
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """
    The attempt's outcome, the answers when it has them, and the message the API returned.
    """
    if result is None:
        return "missing", None, None
    kind = result.get("type")
    if kind in ("expired", "canceled"):
        return str(kind), None, None
    if kind == "errored":
        error = (result.get("error") or {}).get("error") or result.get("error") or {}
        outcome = (
            "error" if error.get("type") == "invalid_request_error" else "server_error"
        )
        return outcome, None, None
    message = result.get("message") or {}
    if message.get("stop_reason") == "refusal":
        return "refusal", None, message
    text = "".join(
        b.get("text", "") for b in message.get("content", []) if b.get("type") == "text"
    )
    try:
        answers = json.loads(text)
    except json.JSONDecodeError:
        return "invalid_output", None, message
    if message.get("stop_reason") == "max_tokens" or not validator.is_valid(answers):
        return "invalid_output", None, message
    return "ok", answers, message


@dataclass
class Judged:
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    outcomes: dict[str, str] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    batches: list[str] = field(default_factory=list)


def judge(
    found: Sequence[Item],
    rubric: Rubric,
    batches: Batches,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Judged:
    system = system_prompt(rubric)
    version = prompt_version(rubric)
    validators = {
        i.item_id: Draft202012Validator(rubric.schema(rubric.applicable(i.facts)))
        for i in found
    }
    judged = Judged()
    pending = list(found)
    for attempt in range(1, ATTEMPTS + 1):
        if not pending:
            break
        batch_id = batches.create([request(i, rubric, system) for i in pending])
        judged.batches.append(batch_id)
        while batches.status(batch_id) != "ended":
            sleep(POLL_S)
        results = dict(batches.results(batch_id))
        again = []
        for item in pending:
            outcome, answers, message = read_result(
                results.get(item.item_id), validators[item.item_id]
            )
            counted = usage(message)
            judged.attempts.append(
                {
                    "item_id": item.item_id,
                    "attempt": attempt,
                    "batch_id": batch_id,
                    "at": now().isoformat(),
                    "provider": PROVIDER,
                    "model_requested": MODEL,
                    "model_returned": (message or {}).get("model"),
                    "prompt_version": version,
                    "rubric": rubric.version,
                    "settings": settings(),
                    "outcome": outcome,
                    "stop_reason": (message or {}).get("stop_reason"),
                    "usage": counted,
                    "cost_usd": cost(counted),
                }
            )
            judged.outcomes[item.item_id] = outcome
            if answers is not None:
                judged.answers[item.item_id] = answers
            elif outcome in RETRIED:
                again.append(item)
        pending = again
    return judged


def settings() -> dict[str, Any]:
    return {
        "effort": EFFORT,
        "thinking": "adaptive",
        "max_tokens": MAX_TOKENS,
        "temperature": None,
        "batch": True,
    }


def judgments(
    found: Sequence[Item], rubric: Rubric, judged: Judged
) -> list[dict[str, Any]]:
    """
    Per item, each applicable question's answer, whether it passes, and whether a person grades it instead: when its
    confidence is one the rubric grades by hand, or when the judge never answered the item.
    """
    kept = []
    for item in found:
        answers = judged.answers.get(item.item_id)
        questions = {}
        for q in rubric.applicable(item.facts):
            given = (answers or {}).get(q.id)
            if given is None:
                questions[q.id] = {"answer": None, "passes": None, "by_hand": True}
                continue
            questions[q.id] = {
                "answer": given["answer"],
                "confidence": given["confidence"],
                "reason": given["reason"],
                "passes": q.passes(given["answer"], item.language),
                "by_hand": given["confidence"] in rubric.by_hand,
            }
        kept.append(
            {
                "item_id": item.item_id,
                "outcome": judged.outcomes.get(item.item_id, "missing"),
                "questions": questions,
            }
        )
    return kept


def totals(attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    summed: Counter[str] = Counter()
    for a in attempts:
        for k, v in a["usage"].items():
            summed[k] += v or 0
    costs = [a["cost_usd"] for a in attempts if a["cost_usd"] is not None]
    return {**dict(summed), "cost_usd": round(sum(costs), 6)}


def block(rubric: Rubric, judged: Judged) -> dict[str, Any]:
    """
    The judge as a run manifest names it (ADR-0005, The run manifest).
    """
    return {
        "provider": PROVIDER,
        "model_requested": MODEL,
        "model_returned": sorted(
            {a["model_returned"] for a in judged.attempts if a["model_returned"]}
        ),
        "settings": settings(),
        "prompt_version": prompt_version(rubric),
        "rubric": {"version": rubric.version, "sha256": rubric.sha256},
        "price": {
            "per_million": dict(
                zip(
                    ("input", "output", "cache_write", "cache_read"),
                    PRICES[MODEL],
                    strict=True,
                )
            ),
            "batch_share": BATCH_SHARE,
            "on": PRICED_ON,
        },
        "seed": None,
        "batches": list(judged.batches),
    }


def keep(
    out: Path,
    found: Sequence[Item],
    rubric: Rubric,
    judged: Judged,
    source: Mapping[str, Any],
    code: Mapping[str, Any],
    times: tuple[datetime, datetime],
) -> dict[str, Any]:
    """
    Writes the items, the attempts, the judgments, and the manifest that hashes them; returns the manifest.
    """
    out.mkdir(parents=True, exist_ok=True)
    kept = judgments(found, rubric, judged)
    hashes = {"items.jsonl": write_items(out / "items.jsonl", found)}
    for name, rows in (("attempts.jsonl", judged.attempts), ("judgments.jsonl", kept)):
        body = "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows
        )
        (out / name).write_text(body, encoding="utf-8")
        hashes[name] = hashlib.sha256(body.encode()).hexdigest()
    manifest = {
        "run": out.name,
        "purpose": "judge",
        "started_at": times[0].isoformat(),
        "ended_at": times[1].isoformat(),
        "code": dict(code),
        "source": dict(source),
        "judge": block(rubric, judged),
        "items": len(found),
        "attempts": len(judged.attempts),
        "outcomes": dict(Counter(judged.outcomes.values())),
        "by_hand": sum(q["by_hand"] for j in kept for q in j["questions"].values()),
        "totals": totals(judged.attempts),
        "results": hashes,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def estimate(found: Sequence[Item], rubric: Rubric) -> dict[str, Any]:
    """
    What judging the items would cost at the batch's list price, from the prompt's length and set allowances for the
    judge's output, before any call; the attempts record what it did cost.
    """
    system = system_prompt(rubric)
    price_in, price_out, _, _ = PRICES[MODEL]
    input_tokens = output_tokens = 0
    for item in found:
        asked = rubric.applicable(item.facts)
        text = system + rendered(item, asked) + json.dumps(rubric.schema(asked))
        input_tokens += round(len(text) / CHARACTERS_PER_TOKEN)
        output_tokens += THINKING_PER_REPLY + OUTPUT_PER_QUESTION * len(asked)
    usd = (
        (input_tokens * price_in + output_tokens * price_out) * BATCH_SHARE / 1_000_000
    )
    return {
        "items": len(found),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(usd, 4),
        "priced_on": PRICED_ON,
    }


def source(
    run: Path | None, items_path: Path | None, found: Sequence[Item]
) -> dict[str, Any]:
    digest = hashlib.sha256(
        "".join(json.dumps(i.to_json(), sort_keys=True) for i in found).encode()
    ).hexdigest()
    return {
        "run": run.name if run is not None else None,
        "items_file": str(items_path) if items_path is not None else None,
        "items": len(found),
        "items_sha256": digest,
    }
