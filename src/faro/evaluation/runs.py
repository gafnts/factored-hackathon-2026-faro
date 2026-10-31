"""
A set played in process on a machine that holds the snapshot (ADR-0005's amendment of 2026-10-01): each case through
the player over the tools' items for its customer, a development set's with the scripted models or the deterministic
baseline's, and the held-out set's with the baseline's alone (ADR-0005, Baselines), graded, and its evidence and grade kept under data/evaluation/runs/<run>/, never committed, since
both hold the case's values (SEC-03). The summary holds counts, and the findings no open disagreement entry matches by
situation and check only, so it can be printed. The run's manifest says what ran (ADR-0005, The run manifest): no
provider and no latency, since no model runs and the network is skipped, and a cost of zero.
"""

import asyncio
import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from faro.agent.models import Factory
from faro.evaluation import (
    baseline,
    bronze,
    cases,
    disagreements,
    families,
    generator,
    grader,
    heldout,
    metrics,
    oracle,
    player,
)
from faro.evaluation.scripted import ScriptedModels

MODELS = ("scripted", "baseline")


class PlayError(ValueError):
    pass


INDEX_PAGE = Path("docs/evaluation/runs.md")
REPORTED = Path("docs/evaluation/runs")


def index(reported: Path = REPORTED, page: Path = INDEX_PAGE) -> int:
    """
    The run index (ADR-0005, The run manifest): one row per reported run, generated from the files committed under
    docs/evaluation/runs/, each holding a run's purpose with the manifest and the summary its keeper wrote. Returns
    how many rows it wrote.
    """
    committed = sorted(reported.glob("*.json")) if reported.is_dir() else []
    rows = []
    for path in committed:
        body = json.loads(path.read_text(encoding="utf-8"))
        manifest, summary = body["manifest"], body["summary"]
        cost = (manifest.get("totals") or {}).get("cost_usd")
        row = (
            manifest["run"],
            manifest["started_at"][:10],
            body["purpose"],
            manifest["mode"],
            f"{manifest['set']['name']} ({manifest['set']['cases']})",
            manifest["stack"]["environment"],
            str(manifest["grader"]),
            f"{summary['passed']} of {summary['cases']}",
            "" if cost is None else f"{cost:.2f}",
        )
        rows.append("| " + " | ".join(row) + " |")
    lines = [
        "# Runs",
        "",
        "Generated from the files under `runs/` by `make eval-index`; do not edit. One row per reported run",
        "(ADR-0005, The run manifest), each an offline measurement on our cases; the per-case results that a",
        "manifest's hashes name stay in the evaluation bucket.",
        "",
    ]
    if rows:
        lines += [
            "| Run | Date | Purpose | Mode | Set (cases) | Stack | Grader | Passed | Cost (USD) |",
            "|---|---|---|---|---|---|---|---|---|",
            *rows,
        ]
    else:
        lines.append("No reported run has been committed yet.")
    page.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def run_id(now: datetime | None = None) -> str:
    at = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{at}-{uuid.uuid4().hex[:4]}"


def summarize(
    graded: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    uncovered: Counter[str] = Counter()
    covered: Counter[str] = Counter()
    for case, grade in graded:
        for finding in [*grade["divergence"], *grade["failures"]]:
            entry = disagreements.covers(entries, case, finding)
            if entry is None:
                uncovered[f"{case['situation']} {finding['check']}"] += 1
            else:
                covered[entry] += 1
    grades = [g for _, g in graded]
    return {
        "cases": len(grades),
        "passed": sum(bool(g["passed"]) for g in grades),
        "errors": sum(g["error"] is not None for g in grades),
        "diverged": sum(g["diverged_at"] is not None for g in grades),
        "unsafe": sum(bool(g["safety"]) for g in grades),
        "safety": dict(Counter(f["check"] for g in grades for f in g["safety"])),
        "covered": dict(sorted(covered.items())),
        "uncovered": dict(sorted(uncovered.items())),
    }


Played = list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def answered(played: Played) -> list[dict[str, Any]]:
    """
    Per node and purpose, the calls each model answered, as the record names the model and its provider.
    """
    calls: Counter[tuple[str, str, str, str | None]] = Counter(
        (e["node"], e["purpose"], e["model_requested"], e["provider"])
        for _, evidence, _ in played
        for e in evidence["record"]
        if e["kind"] == "model_call"
    )
    return [
        {
            "node": node,
            "purpose": purpose,
            "model": model,
            "provider": provider,
            "calls": n,
        }
        for (node, purpose, model, provider), n in sorted(calls.items())
    ]


def totals(played: Played) -> dict[str, Any]:
    closed = [
        e["totals"]
        for _, evidence, _ in played
        for e in evidence["record"]
        if e["kind"] == "turn_closed"
    ]
    costs = [t["cost_usd"] for t in closed]
    return {
        "turns": sum(len(grader.record_turns(e["record"])) for _, e, _ in played),
        **{
            k: sum(t[k] for t in closed)
            for k in ("model_calls", "tool_calls", "input_tokens", "output_tokens")
        },
        "cost_usd": None if None in costs else round(sum(costs), 6),
    }


def manifest(
    run: str,
    times: tuple[datetime, datetime],
    code: Mapping[str, Any],
    versions: Mapping[str, Any],
    set_name: str,
    drawn: Sequence[Mapping[str, Any]],
    aside: int,
    models: str,
    played: Played,
    results: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "run": run,
        "mode": "in_process",
        "started_at": times[0].isoformat(),
        "ended_at": times[1].isoformat(),
        "code": dict(code),
        "versions": dict(versions),
        "stack": {"environment": "in_process"},
        "set": {
            "name": set_name,
            "sha256": hashlib.sha256(
                "".join(generator.digest(c) for c in drawn).encode()
            ).hexdigest(),
            "cases": len(drawn) - aside,
            "set_aside": aside,
        },
        "oracle": {"policy": oracle.POLICY_VERSION},
        "grader": grader.VERSION,
        "system": models,
        "judge": None,
        "models": answered(played),
        "fixtures": digest([c["fixtures"] for c, _, _ in played]),
        "faults": digest([c["faults"] for c, _, _ in played]),
        "parallelism": 1,
        "repeats": 1,
        "totals": totals(played),
        "results": dict(sorted(results.items())),
    }


def play_set(
    set_path: Path,
    database: Path,
    out: Path,
    versions: Mapping[str, Any],
    models: str = "scripted",
    code: Mapping[str, Any] | None = None,
    held: tuple[Mapping[str, Any], bool] | None = None,
) -> dict[str, Any]:
    """
    code is the commit the run's code is at, and whether the tree held changes besides. held is a held-out set's
    manifest and whether it is committed: the held-out side plays in process with the baseline alone (ADR-0005,
    Baselines), and only as its committed manifest describes it (heldout.py).
    """
    started = datetime.now(UTC)
    every = list(cases.read(set_path))
    sides = {c["side"] for c in every}
    if sides == {"held_out"}:
        if models != "baseline":
            raise PlayError(
                f"the held-out side plays in process with the baseline alone, not the {models} models"
            )
        if held is None:
            raise PlayError("a held-out set plays only with its committed manifest")
        try:
            heldout.verify(every, *held)
        except heldout.HeldOutError as error:
            raise PlayError(str(error)) from error
    elif sides - {"development"}:
        raise PlayError("a set holds cases of one side of the split")
    # Access and expired-session cases are decided by the deployed Gateway or authorizer, so they play only end to end
    # and count for both systems (ADR-0005, Baselines); the summary says how many were set aside.
    aside = sum(c["situation"].startswith(metrics.HARNESS) for c in every)
    drawn = [c for c in every if not c["situation"].startswith(metrics.HARNESS)]
    loaded, answers = families.load(), families.load_answers()
    by_family = {f.family_id: f for f in loaded}
    by_answer = {a.answer_id: a for a in answers}
    items = bronze.tools_items(database, {c["customer_id"] for c in drawn})
    meta = [i for i in items if i["pk"] == "META"]
    by_customer: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_customer.setdefault(item["pk"], []).append(item)

    async def play_all() -> Played:
        found = []
        for case in drawn:
            held = meta + by_customer.get(case["customer_id"], [])
            factory: Factory = (
                baseline.factory
                if models == "baseline"
                else ScriptedModels(case, by_family, by_answer, held)
            )
            evidence = await player.play(case, held, factory)
            found.append((case, evidence, grader.grade(case, evidence)))
        return found

    played = asyncio.run(play_all())
    out.mkdir(parents=True, exist_ok=True)
    results = {
        "evidence.jsonl": "".join(
            json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n"
            for _, e, _ in played
        ),
        "grades.jsonl": "".join(
            json.dumps(g, ensure_ascii=False, sort_keys=True) + "\n"
            for _, _, g in played
        ),
    }
    graded = [(c, g) for c, _, g in played]
    counted = totals(played)
    summary = {
        "run": out.name,
        "set": set_path.stem,
        "mode": "in_process",
        "models": models,
        "grader": grader.VERSION,
        "versions": dict(versions),
        "set_aside": aside,
        **summarize(graded, disagreements.load()),
        "metrics": metrics.compute(
            graded, counted, {"turns": counted["turns"], "repeats": 1, "parallelism": 1}
        ),
    }
    results["summary.json"] = json.dumps(summary, indent=2) + "\n"
    for name, body in results.items():
        (out / name).write_text(body, encoding="utf-8")
    kept = manifest(
        out.name,
        (started, datetime.now(UTC)),
        code or {},
        versions,
        set_path.stem,
        every,
        aside,
        models,
        played,
        {n: hashlib.sha256(b.encode()).hexdigest() for n, b in results.items()},
    )
    (out / "manifest.json").write_text(
        json.dumps(kept, indent=2) + "\n", encoding="utf-8"
    )
    return summary
