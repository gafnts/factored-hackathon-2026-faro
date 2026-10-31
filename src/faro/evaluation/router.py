"""
The router comparison (ADR-0005, Baselines: the router, as amended on 2026-10-02; DML-07, DML-11, DML-12). Candidates
read each message of one side of the family split and say which supported requests it holds and whether it holds one.
Per language, the report gives macro F1 and recall per label on the first request in POL-05's order, multi-request
detection, the no-request gate's accuracy, calibration where a candidate gives probabilities, latency and cost per
call, a confusion matrix, and every reading that missed, for reading by hand.

Every figure comes with a 95% percentile bootstrap interval over families, since a family's paraphrases move
together: 1,000 resamples from a fixed, reported seed, within each group, as the split draws a third of each, and the
same resample for every candidate, so the interval of a difference is paired. A candidate beats another only when that
interval excludes zero, and never in a language whose groups hold one family each, since every resample of it is the
same draw.

A candidate is a plug-in: a name, a callable from a message to a Reading, and the settings a manifest records. A
router that answers in code, as the keyword baseline does, becomes one through routed(). The development side tunes:
its folds group families within each group, so no family's paraphrases sit on both sides of a fold, and a candidate
that learns is fitted on the other folds. The held-out side runs once the candidates are frozen, from a clean tree, so
its manifest names the code that ran, and never in folds. Messages and their labels are team-generated (SEC-02), so
the readings and errors keep their text; slots hold made-up values, so no message carries a record's value (SEC-03).
"""

import asyncio
import hashlib
import json
import random
import statistics
import time
from collections import Counter
from collections.abc import Callable, Coroutine, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from faro.agent.models import ORDER, RouterOutput
from faro.evaluation import families, intervals, language
from faro.evaluation.families import Family

NONE = "none"
LABELS: tuple[str, ...] = tuple(ORDER)
SIDES = ("development", "held_out")
FOLDS = 5
PARALLEL = 4
BINS = 10
OUT = Path("data/evaluation/router")
# The figures each pair of candidates is compared on, paired over families.
COMPARED = ("macro_f1", "first_accuracy", "gate_accuracy", "multi_exact")


class RouterError(ValueError):
    pass


@dataclass(frozen=True)
class Reading:
    """
    labels in any order; probabilities, by label, when the candidate gives them; cost_usd None when unknown; failed
    when the candidate gave no reading, which counts as no request found.
    """

    labels: tuple[str, ...]
    has_request: bool
    probabilities: Mapping[str, float] | None = None
    cost_usd: float | None = 0.0
    failed: bool = False


Read = Callable[[str], Coroutine[Any, Any, Reading]]


@dataclass(frozen=True)
class Candidate:
    """
    fit, for a candidate that learns, gives the candidate trained on the messages passed; estimate prices a list of
    texts before any call.
    """

    name: str
    read: Read
    model: str | None = None
    settings: Mapping[str, Any] = field(default_factory=dict)
    fit: Callable[[Sequence["Message"]], "Candidate"] | None = None
    estimate: Callable[[Sequence[str]], Mapping[str, Any]] | None = None


def routed(name: str, route: Callable[[str], RouterOutput]) -> Candidate:
    """
    A candidate from a router that answers in code, at no cost.
    """

    async def read(text: str) -> Reading:
        found = route(text)
        return Reading(tuple(found.requests), found.has_request)

    return Candidate(name, read, settings={"model": None})


@dataclass(frozen=True)
class Message:
    id: str
    family_id: str
    group: str
    kind: str
    language: str
    text: str
    labels: tuple[str, ...]

    @property
    def has_request(self) -> bool:
        return bool(self.labels)


@dataclass(frozen=True)
class Result:
    message: Message
    reading: Reading
    latency_ms: float


def spoken(language_code: str) -> str:
    return language_code if language_code in families.LANGUAGES else "other"


def messages(
    loaded: Sequence[Family], held: frozenset[str], side: str
) -> list[Message]:
    """
    Every message of the side's families, its slots filled with made-up values.
    """
    if side not in SIDES:
        raise RouterError(f"no side named {side}")
    chosen = [f for f in loaded if (f.family_id in held) == (side == "held_out")]
    return [
        Message(
            m.id,
            f.family_id,
            f.group,
            f.kind,
            spoken(m.language),
            language.filled(m.text),
            f.labels,
        )
        for f in chosen
        for m in f.messages
    ]


def families_sha256() -> str:
    digest = hashlib.sha256()
    folder = Path(families.__file__).parent
    for path in sorted(folder.glob("*.json")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


async def read_all(
    candidate: Candidate, found: Sequence[Message], parallel: int = PARALLEL
) -> list[Result]:
    """
    The latency of a call is timed inside the gate, so it counts the call and not the queue.
    """
    gate = asyncio.Semaphore(parallel)

    async def one(message: Message) -> Result:
        async with gate:
            started = time.perf_counter()
            reading = await candidate.read(message.text)
            return Result(message, reading, (time.perf_counter() - started) * 1000)

    return list(await asyncio.gather(*(one(m) for m in found)))


def first(labels: Iterable[str]) -> str:
    present = set(labels)
    return next((label for label in LABELS if label in present), NONE)


def predicted(reading: Reading) -> str:
    return first(reading.labels) if reading.has_request else NONE


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def calibration(results: Sequence[Result]) -> dict[str, float | None]:
    """
    Brier score and expected calibration error over every message and label, for a candidate that gives
    probabilities; none otherwise.
    """
    if not results or any(r.reading.probabilities is None for r in results):
        return {"brier": None, "ece": None}
    pairs = [
        (
            (r.reading.probabilities or {}).get(label, 0.0),
            float(label in r.message.labels),
        )
        for r in results
        for label in LABELS
    ]
    brier = sum((p - y) ** 2 for p, y in pairs) / len(pairs)
    bins: dict[int, list[tuple[float, float]]] = {}
    for p, y in pairs:
        bins.setdefault(min(int(p * BINS), BINS - 1), []).append((p, y))
    ece = sum(
        len(b)
        / len(pairs)
        * abs(sum(p for p, _ in b) / len(b) - sum(y for _, y in b) / len(b))
        for b in bins.values()
    )
    return {"brier": brier, "ece": ece}


def measures(results: Sequence[Result]) -> dict[str, float | None]:
    """
    A candidate's figures over a set of readings. F1 is undefined for a label nothing expects or predicts, and macro
    F1 averages the labels it is defined for.
    """
    if not results:
        return {}
    pairs = Counter((first(r.message.labels), predicted(r.reading)) for r in results)
    expected, said = Counter[str](), Counter[str]()
    for (e, s), n in pairs.items():
        expected[e] += n
        said[s] += n
    found: dict[str, float | None] = {}
    scores = []
    for label in LABELS:
        hit = pairs[(label, label)]
        missed, extra = expected[label] - hit, said[label] - hit
        found[f"recall.{label}"] = hit / (hit + missed) if hit + missed else None
        if hit + missed + extra:
            scores.append(2 * hit / (2 * hit + missed + extra))
    found["macro_f1"] = mean(scores)
    found["first_accuracy"] = sum(n for (e, s), n in pairs.items() if e == s) / len(
        results
    )
    found["gate_accuracy"] = mean(
        [float(r.reading.has_request == r.message.has_request) for r in results]
    )
    several = [r for r in results if len(r.message.labels) > 1]
    found["multi_detected"] = mean(
        [float(len(set(r.reading.labels)) > 1) for r in several]
    )
    found["multi_exact"] = mean(
        [float(set(r.reading.labels) == set(r.message.labels)) for r in several]
    )
    single = [r for r in results if len(r.message.labels) == 1]
    found["false_multi"] = mean([float(len(set(r.reading.labels)) > 1) for r in single])
    found["latency_ms"] = mean([r.latency_ms for r in results])
    costs = [r.reading.cost_usd for r in results]
    found["cost_usd"] = (
        None if None in costs else mean([c for c in costs if c is not None])
    )
    found["failed"] = mean([float(r.reading.failed) for r in results])
    found.update(calibration(results))
    return found


def confusion(results: Sequence[Result]) -> dict[str, dict[str, int]]:
    """
    The first request expected against the first said, counts by label, none for no request.
    """
    found: dict[str, Counter[str]] = {}
    for r in results:
        found.setdefault(first(r.message.labels), Counter())[predicted(r.reading)] += 1
    return {e: dict(sorted(c.items())) for e, c in sorted(found.items())}


def errors(name: str, results: Sequence[Result]) -> list[dict[str, Any]]:
    return [
        {
            "candidate": name,
            "id": r.message.id,
            "family_id": r.message.family_id,
            "kind": r.message.kind,
            "language": r.message.language,
            "text": r.message.text,
            "expected": list(r.message.labels),
            "said": list(r.reading.labels),
            "has_request": [r.message.has_request, r.reading.has_request],
            "failed": r.reading.failed,
        }
        for r in results
        if predicted(r.reading) != first(r.message.labels)
        or set(r.reading.labels) != set(r.message.labels)
        or r.reading.has_request != r.message.has_request
    ]


def paired(
    by_candidate: Mapping[str, Sequence[Result]],
    resamples: int = intervals.RESAMPLES,
    seed: int = intervals.SEED,
) -> dict[str, intervals.Estimate]:
    """
    Every candidate's figures, and each later candidate's difference from each earlier one on COMPARED, all from one
    resample of families within groups.
    """
    names = list(by_candidate)
    by_family: dict[str, dict[str, list[Result]]] = {}
    group: dict[str, str] = {}
    for name, results in by_candidate.items():
        for r in results:
            by_family.setdefault(r.message.family_id, {}).setdefault(name, []).append(r)
            group[r.message.family_id] = r.message.group

    def measure(drawn: Sequence[str]) -> dict[str, float | None]:
        figures: dict[str, dict[str, float | None]] = {
            name: measures([r for f in drawn for r in by_family[f].get(name, [])])
            for name in names
        }
        found = {
            f"{name}.{k}": v
            for name, values in figures.items()
            for k, v in values.items()
        }
        for n, later in enumerate(names):
            for earlier in names[:n]:
                for k in COMPARED:
                    a, b = figures[later].get(k), figures[earlier].get(k)
                    found[f"{later} - {earlier}.{k}"] = (
                        None if a is None or b is None else a - b
                    )
        return found

    return intervals.bootstrap(
        sorted(by_family), measure, resamples, seed, strata=lambda f: group[f]
    )


def verdict(estimate: intervals.Estimate, later: str, earlier: str) -> str:
    if not estimate.excludes_zero():
        return "tie"
    assert estimate.low is not None
    return later if estimate.low > 0 else earlier


def by_language(
    by_candidate: Mapping[str, Sequence[Result]], resamples: int = intervals.RESAMPLES
) -> dict[str, Any]:
    names = list(by_candidate)
    spoken_in = sorted({r.message.language for rs in by_candidate.values() for r in rs})
    found: dict[str, Any] = {}
    for code in spoken_in:
        sliced = {
            n: [r for r in rs if r.message.language == code]
            for n, rs in by_candidate.items()
        }
        estimates = paired(sliced, resamples)
        any_results = sliced[names[0]]
        groups = {r.message.family_id: r.message.group for r in any_results}
        varies = max(Counter(groups.values()).values(), default=0) > 1
        found[code] = {
            "messages": len(any_results),
            "families": len(groups),
            "candidates": {
                n: {
                    k.removeprefix(f"{n}."): e.to_json()
                    for k, e in estimates.items()
                    if k.startswith(f"{n}.")
                }
                for n in names
            },
            "differences": {
                f"{later} - {earlier}": {
                    k: {
                        **estimates[f"{later} - {earlier}.{k}"].to_json(),
                        "beats": verdict(
                            estimates[f"{later} - {earlier}.{k}"], later, earlier
                        )
                        if varies
                        else "too_few_families",
                    }
                    for k in COMPARED
                }
                for i, later in enumerate(names)
                for earlier in names[:i]
            },
            "confusion": {n: confusion(rs) for n, rs in sliced.items()},
        }
    return found


def folds(
    found: Sequence[Message], k: int = FOLDS, seed: int = intervals.SEED
) -> list[frozenset[str]]:
    """
    The families of each fold: within each group, shuffled from the seed and dealt in turn, so a family's messages
    stay in one fold and every fold holds every group.
    """
    groups: dict[str, list[str]] = {}
    for m in found:
        if m.family_id not in groups.setdefault(m.group, []):
            groups[m.group].append(m.family_id)
    rng = random.Random(seed)
    dealt: list[set[str]] = [set() for _ in range(k)]
    for name in sorted(groups):
        ids = sorted(groups[name])
        rng.shuffle(ids)
        for n, family_id in enumerate(ids):
            dealt[n % k].add(family_id)
    return [frozenset(d) for d in dealt]


async def cross_validate(
    candidates: Sequence[Candidate],
    found: Sequence[Message],
    read: Mapping[str, Sequence[Result]],
    k: int,
    parallel: int,
) -> list[dict[str, Any]]:
    """
    Per fold, each candidate's figures on the fold's families: a candidate that learns is fitted on the other folds
    and reads the fold again; one that doesn't is scored on the readings it already gave.
    """
    reported = []
    for n, held in enumerate(folds(found, k)):
        figures = {}
        for candidate in candidates:
            if candidate.fit is not None:
                fitted = candidate.fit([m for m in found if m.family_id not in held])
                results = await read_all(
                    fitted, [m for m in found if m.family_id in held], parallel
                )
            else:
                results = [
                    r for r in read[candidate.name] if r.message.family_id in held
                ]
            values = measures(results)
            figures[candidate.name] = {key: values.get(key) for key in COMPARED}
        reported.append({"fold": n + 1, "families": len(held), "candidates": figures})
    return reported


def fold_summary(reported: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    names = reported[0]["candidates"] if reported else {}
    for name in names:
        found[name] = {}
        for key in COMPARED:
            values = [
                f["candidates"][name][key]
                for f in reported
                if f["candidates"][name][key] is not None
            ]
            found[name][key] = {
                "mean": round(statistics.fmean(values), 4) if values else None,
                "low": round(min(values), 4) if values else None,
                "high": round(max(values), 4) if values else None,
            }
    return found


def estimate(
    candidates: Sequence[Candidate], found: Sequence[Message]
) -> dict[str, Any]:
    texts = [m.text for m in found]
    return {
        c.name: dict(c.estimate(texts))
        if c.estimate is not None
        else {"calls": len(texts), "cost_usd": 0.0}
        for c in candidates
    }


def write_lines(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    body = "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows
    )
    path.write_text(body, encoding="utf-8")
    return hashlib.sha256(body.encode()).hexdigest()


async def compare(
    candidates: Sequence[Candidate],
    side: str,
    out: Path,
    code: Mapping[str, Any],
    calls: Sequence[Mapping[str, Any]] = (),
    k: int | None = None,
    parallel: int = PARALLEL,
    resamples: int = intervals.RESAMPLES,
) -> dict[str, Any]:
    """
    Reads the side's messages with each candidate, keeps the readings, the errors, the calls each candidate recorded,
    and the report, which is the run's manifest; returns the report. calls is the list the candidates record into,
    read once they are done.
    """
    if side == "held_out" and k is not None:
        raise RouterError("the held-out side is never tuned, so it runs in no fold")
    if side == "held_out" and not code.get("clean"):
        raise RouterError(
            "the held-out side runs from a clean tree, so its manifest names the code that ran"
        )
    if len({c.name for c in candidates}) != len(candidates) or not candidates:
        raise RouterError("name each candidate once")
    loaded, answers = families.load(), families.load_answers()
    found = messages(loaded, families.held_out_ids(loaded, answers), side)
    started = datetime.now(UTC)
    read = {c.name: await read_all(c, found, parallel) for c in candidates}
    ended = datetime.now(UTC)
    out.mkdir(parents=True, exist_ok=True)
    hashes = {
        "readings.jsonl": write_lines(
            out / "readings.jsonl",
            (
                {
                    "candidate": n,
                    "id": r.message.id,
                    "latency_ms": round(r.latency_ms, 1),
                    **asdict(r.reading),
                }
                for n, rs in read.items()
                for r in rs
            ),
        ),
        "errors.jsonl": write_lines(
            out / "errors.jsonl", (e for n, rs in read.items() for e in errors(n, rs))
        ),
        "calls.jsonl": write_lines(out / "calls.jsonl", list(calls)),
    }
    report: dict[str, Any] = {
        "run": out.name,
        "purpose": "router_comparison",
        "side": side,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "code": dict(code),
        "families": {
            "sha256": families_sha256(),
            "families": len({m.family_id for m in found}),
            "messages": len(found),
        },
        "candidates": [
            {
                "name": c.name,
                "model": c.model,
                "settings": dict(c.settings),
                "learns": c.fit is not None,
            }
            for c in candidates
        ],
        "bootstrap": {
            "unit": "family",
            "strata": "group",
            "resamples": resamples,
            "seed": intervals.SEED,
            "level": intervals.LEVEL,
        },
        "parallel": parallel,
        "by_language": by_language(read, resamples),
        "totals": {
            n: {
                "calls": len(rs),
                "failed": sum(r.reading.failed for r in rs),
                "cost_usd": round(sum(r.reading.cost_usd or 0.0 for r in rs), 6),
            }
            for n, rs in read.items()
        },
        "results": hashes,
    }
    if k is not None:
        reported = await cross_validate(candidates, found, read, k, parallel)
        report["folds"] = {
            "k": k,
            "seed": intervals.SEED,
            "per_fold": reported,
            "summary": fold_summary(reported),
        }
    (out / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report
