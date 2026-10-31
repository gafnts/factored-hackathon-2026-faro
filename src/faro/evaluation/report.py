"""
The results page (ADR-0005, Reporting; EVL-08 to EVL-13): M-01 to M-05 over the reported runs of one set, computed
again from the grades each run kept, never from a summary alone, with pass^k and the spread over the runs, then by
language, by segment and by country (joined from the snapshot, since a case names its customer and nothing else), by
group, by rule, by source, and by phrasing, every cell with its sample size and a Wilson interval and any cell under
10 cases suppressed (SEC-03). Every failed case is counted and classified (EVL-09), the baseline is compared on the
cases both played (EVL-01), the judge's answers are joined by item where a judge run and the agreement report are
given (EVL-10), and the hypothesis on unsafe outcomes is tested as stated before the first run (CTL-04). What is
written is counts and rates alone; a case's values stay in the run.
"""

import hashlib
import json
import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from faro.evaluation import (
    bronze,
    cases,
    disagreements,
    generator,
    metrics,
    rubric,
)

RESULTS = Path("docs/evaluation/results.json")
PAGE = Path("docs/evaluation/results.md")
SETS = Path("data/evaluation/sets")
SET_DOCS = Path("docs/evaluation/sets")
SUPPRESSED = "suppressed"
MIN_CELL = 10
# The classes a failed case falls in, in the order the first that fits is taken (EVL-09).
KINDS = ("error", "unsafe", "diverged", "language", "fact", "handoff", "other")

Graded = list[tuple[Mapping[str, Any], Mapping[str, Any]]]
Measure = Callable[
    [Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]]], dict[str, Any]
]


class ReportError(ValueError):
    pass


@dataclass(frozen=True)
class Run:
    path: Path
    manifest: dict[str, Any]
    summary: dict[str, Any]
    graded: Graded
    cost_usd: float | None = None
    unpriced_calls: int = 0

    @property
    def run_id(self) -> str:
        return str(self.manifest["run"])

    @property
    def set_name(self) -> str:
        return str(self.manifest["set"]["name"])


@dataclass(frozen=True)
class Profile:
    """
    What the snapshot says of a case's customer, joined by ID: the segment, the country, and whether any card of
    theirs is flagged as updated after the as-of instant (ADR-0004).
    """

    segment: str
    country: str
    updated_after_as_of: bool


def load_run(path: Path, sets: Path = SETS) -> Run:
    """
    A run's grades, joined to the cases of the set its manifest names, which must hash as the manifest says.
    """
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    set_path = sets / f"{manifest['set']['name']}.jsonl"
    if not set_path.is_file():
        raise ReportError(f"no drawn set at {set_path} for run {manifest['run']}")
    drawn = {c["case_id"]: c for c in cases.read(set_path)}
    digest = hashlib.sha256(
        "".join(generator.digest(c) for c in drawn.values()).encode()
    ).hexdigest()
    if digest != manifest["set"]["sha256"]:
        raise ReportError(
            f"the drawn set at {set_path} isn't the one run {manifest['run']} played"
        )
    grades: list[dict[str, Any]] = []
    if (path / "grades.jsonl").is_file():
        with (path / "grades.jsonl").open(encoding="utf-8") as kept:
            grades = [json.loads(line) for line in kept if line.strip()]
    else:
        for kept_case in sorted((path / "cases").glob("*.json")):
            grades.append(json.loads(kept_case.read_text(encoding="utf-8"))["grade"])
    if not grades:
        raise ReportError(f"run {manifest['run']} kept no grades")
    missing = [g["case_id"] for g in grades if g["case_id"] not in drawn]
    if missing:
        raise ReportError(
            f"run {manifest['run']} graded {len(missing)} cases the set doesn't hold"
        )
    cost, unpriced = priced(path, manifest)
    return Run(
        path,
        manifest,
        summary,
        [(drawn[g["case_id"]], g) for g in grades],
        cost,
        unpriced,
    )


def priced(path: Path, manifest: Mapping[str, Any]) -> tuple[float | None, int]:
    """
    The run's model cost and the number of model calls without a price. The manifest's total is void when any call
    has none (a failed attempt the harness retried carries no usage), so the priced calls are summed from the
    evidence instead and the unpriced ones counted, for the page to say.
    """
    total = (manifest.get("totals") or {}).get("cost_usd")
    if total is not None:
        return float(total), 0
    records: list[list[dict[str, Any]]] = []
    if (path / "evidence.jsonl").is_file():
        with (path / "evidence.jsonl").open(encoding="utf-8") as kept:
            records = [
                json.loads(line).get("record") or [] for line in kept if line.strip()
            ]
    elif (path / "cases").is_dir():
        records = [
            json.loads(kept_case.read_text(encoding="utf-8"))["evidence"].get("record")
            or []
            for kept_case in sorted((path / "cases").glob("*.json"))
        ]
    calls: list[dict[str, Any]] = [
        e for record in records for e in record if e.get("kind") == "model_call"
    ]
    if not calls:
        return None, 0
    found = [e["cost_usd"] for e in calls if e.get("cost_usd") is not None]
    if not found:
        return None, len(calls)
    return round(sum(found), 6), len(calls) - len(found)


def profiles(database: Path, side: str, customers: set[str]) -> dict[str, Profile]:
    """
    The join from the snapshot's bronze, for the set's customers alone; the card flag as the tools' items carry it.
    """
    with bronze.connect(database, side) as con:
        con.execute("create temp table wanted (customer_id varchar)")
        con.executemany(
            "insert into wanted values (?)", [(c,) for c in sorted(customers)]
        )
        rows = con.execute(
            "select customer_id, segment, country from customers "
            "where customer_id in (select customer_id from wanted) order by 1"
        ).fetchall()
    updated: set[str] = set()
    for item in bronze.tools_items(database, customers):
        if item.get("kind") == "card" and item.get("updated_after_as_of"):
            updated.add(str(item["pk"]))
    return {
        customer: Profile(str(segment), str(country), customer in updated)
        for customer, segment, country in rows
    }


def talks(graded: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]]) -> Graded:
    return [(c, g) for c, g in graded if metrics.conversation(c)]


MEASURES: dict[str, tuple[Measure, bool]] = {
    # The measure, and whether it reads conversations alone (metrics.compute).
    "M-01": (metrics.m01, True),
    "M-03": (metrics.m03, True),
    "M-04": (metrics.m04, False),
}


def headline(measure: Mapping[str, Any]) -> dict[str, Any]:
    return {k: measure[k] for k in ("count", "cases", "rate", "interval")}


def cell(runs: Sequence[Graded], name: str) -> dict[str, Any]:
    """
    One metric over the runs of one slice: the mean count over the runs at the slice's size, with its interval, and
    the spread of the runs' rates; suppressed under 10 cases, so no slice singles a customer out.
    """
    measure, conversations = MEASURES[name]
    per_run = [measure(talks(run) if conversations else run) for run in runs]
    sizes = [found["cases"] for found in per_run]
    n = min(sizes) if sizes else 0
    if n < MIN_CELL:
        return {"cases": n, "rate": SUPPRESSED}
    rates = [found["rate"] for found in per_run if isinstance(found["rate"], float)]
    mean = sum(found["count"] for found in per_run) / len(per_run)
    count = int(mean) if mean == int(mean) else round(mean, 1)
    return {
        **headline(metrics.rate(count, n)),
        "runs": len(runs),
        "spread": [min(rates), max(rates)] if rates else metrics.NOT_DEFINED,
    }


def sliced(
    runs: Sequence[Graded], key: Callable[[Mapping[str, Any]], Sequence[str]]
) -> dict[str, dict[str, Any]]:
    """
    The three metrics per value of a key; a case under several values (its rules) counts under each.
    """
    values = sorted({v for run in runs for c, _ in run for v in key(c)})
    found = {}
    for value in values:
        slices = [[(c, g) for c, g in run if value in key(c)] for run in runs]
        found[value] = {
            "cases": min(len(s) for s in slices),
            **{name: cell(slices, name) for name in MEASURES},
        }
    return found


def classify(grade: Mapping[str, Any]) -> str | None:
    """
    A failed case's class (EVL-09): the first of error, unsafe, diverged (the router or the flow left the oracle's
    path), language, fact (a fact missing or a figure no fact holds), handoff (its payload), or other.
    """
    if grade["passed"]:
        return None
    if grade["error"] is not None:
        return "error"
    if grade["safety"]:
        return "unsafe"
    if grade["diverged_at"] is not None:
        return "diverged"
    checks = {f["check"] for f in grade["failures"]}
    if "language" in checks:
        return "language"
    if checks & {"fact", "extra_figure", "withheld"}:
        return "fact"
    if any(c.startswith("handoff") for c in checks):
        return "handoff"
    return "other"


def failures(
    runs: Sequence[Run], entries: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """
    Every failed case counted and classified: per run by class and by group, and in cases over the runs, each under
    every class it showed; findings by the disagreement entry that matches them, or by situation and check when none.
    """
    per_run = []
    in_cases: dict[str, set[str]] = {}
    covered: Counter[str] = Counter()
    uncovered: Counter[str] = Counter()
    for run in runs:
        by_class: Counter[str] = Counter()
        by_group: Counter[str] = Counter()
        for case, grade in run.graded:
            kind = classify(grade)
            if kind is None:
                continue
            by_class[kind] += 1
            by_group[case["group"]] += 1
            in_cases.setdefault(case["case_id"], set()).add(kind)
            for finding in [*grade["divergence"], *grade["failures"]]:
                entry = disagreements.covers(entries, case, finding)
                if entry is None:
                    uncovered[f"{case['situation']} {finding['check']}"] += 1
                else:
                    covered[entry] += 1
        per_run.append(
            {
                "run": run.run_id,
                "failed": sum(by_class.values()),
                "cases": len(run.graded),
                "by_class": {k: by_class[k] for k in KINDS if by_class[k]},
                "by_group": dict(sorted(by_group.items())),
            }
        )
    return {
        "per_run": per_run,
        "in_cases": {
            "failed_in_any_run": len(in_cases),
            "by_class": {
                k: sum(k in kinds for kinds in in_cases.values())
                for k in KINDS
                if any(k in kinds for kinds in in_cases.values())
            },
        },
        "by_entry": dict(sorted(covered.items())),
        "unmatched": dict(sorted(uncovered.items())),
    }


def paired(system: Run, baseline: Run) -> dict[str, Any]:
    """
    The baseline against the system's first run on the cases both played (EVL-01): M-01 and M-04 side by side, and
    the paired difference in M-01 with a Wald interval from the discordant cases.
    """
    theirs = {c["case_id"]: (c, g) for c, g in talks(baseline.graded)}
    both = [
        (c, g, theirs[c["case_id"]][1])
        for c, g in talks(system.graded)
        if c["case_id"] in theirs
    ]
    n = len(both)
    only_system = sum(
        metrics.resolved(c, g) and not metrics.resolved(c, b) for c, g, b in both
    )
    only_baseline = sum(
        metrics.resolved(c, b) and not metrics.resolved(c, g) for c, g, b in both
    )
    difference: dict[str, Any] = {"cases": n, "value": metrics.NOT_DEFINED}
    if n:
        d = (only_system - only_baseline) / n
        se = math.sqrt(max(0.0, (only_system + only_baseline) - n * d * d)) / n
        difference = {
            "cases": n,
            "value": round(d, 4),
            "interval": [round(d - metrics.Z * se, 4), round(d + metrics.Z * se, 4)],
            "only_system": only_system,
            "only_baseline": only_baseline,
        }
    return {
        "baseline_run": baseline.run_id,
        "system_run": system.run_id,
        "common_cases": n,
        "M-01": {
            "system": headline(metrics.m01(talks([(c, g) for c, g, _ in both]))),
            "baseline": headline(metrics.m01(talks([(c, b) for c, _, b in both]))),
            "difference": difference,
        },
        "M-03": {
            "system": headline(metrics.m03([(c, g) for c, g, _ in both])),
            "baseline": headline(metrics.m03([(c, b) for c, _, b in both])),
        },
        "M-04": {
            "system": headline(metrics.m04([(c, g) for c, g, _ in both])),
            "baseline": headline(metrics.m04([(c, b) for c, _, b in both])),
        },
    }


def hypothesis(runs: Sequence[Run]) -> dict[str, Any]:
    """
    CTL-04, as ADR-0005 states it: disclosures and unauthorized actions stay at zero over every run; a materially
    incorrect outcome may move. In cases, each counted if any of its runs showed the kind.
    """
    by_case: dict[str, set[str]] = {}
    for run in runs:
        for case, grade in run.graded:
            by_case.setdefault(case["case_id"], set()).update(
                metrics.kinds(case, grade)
            )
    n = len(by_case)
    counts = {
        kind: sum(kind in found for found in by_case.values())
        for kind in ("disclosure", "action", "incorrect", "unverified")
    }
    refuted = counts["disclosure"] + counts["action"] > 0
    return {
        "cases": n,
        "runs": len(runs),
        "by_kind": {kind: metrics.rate(k, n) for kind, k in counts.items()},
        "bound_at_zero": round(3 / n, 4) if n else metrics.NOT_DEFINED,
        "verdict": "refuted" if refuted else "held",
    }


def judged_items(judged: Path) -> dict[str, dict[str, Any]]:
    with (judged / "judgments.jsonl").open(encoding="utf-8") as kept:
        return {j["item_id"]: j for j in map(json.loads, kept) if j.get("item_id")}


def judge_section(
    judged: Sequence[Path],
    agreement: Path | None,
    loaded: rubric.Rubric,
) -> dict[str, Any]:
    """
    The judge's answers over the runs' replies, per question: items, passes, and how many a person grades instead;
    used only where the agreement report lets the judge grade that question (EVL-10), else listed and marked.
    """
    verdicts: dict[str, str] = {}
    scored: dict[str, Any] | None = None
    if agreement is not None:
        scored = json.loads(agreement.read_text(encoding="utf-8"))
        verdicts = {q["question"]: q["verdict"] for q in scored["questions"]}
    per_run = []
    for path in judged:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        answers: dict[str, Counter[str]] = {}
        questions: dict[str, dict[str, Any]] = {}
        for judgment in judged_items(path).values():
            for name, found in judgment["questions"].items():
                tally = questions.setdefault(
                    name, {"items": 0, "passes": 0, "fails": 0, "by_hand": 0}
                )
                tally["items"] += 1
                if found.get("by_hand"):
                    tally["by_hand"] += 1
                if found.get("passes") is True:
                    tally["passes"] += 1
                elif found.get("passes") is False:
                    tally["fails"] += 1
                if found.get("answer") is not None:
                    answers.setdefault(name, Counter())[str(found["answer"])] += 1
        for name, tally in questions.items():
            decided = tally["passes"] + tally["fails"]
            tally["pass_rate"] = headline(metrics.rate(tally["passes"], decided))
            tally["answers"] = dict(sorted(answers.get(name, Counter()).items()))
            tally["used"] = verdicts.get(name) == "judge"
            tally["verdict"] = verdicts.get(name, "not validated")
        per_run.append(
            {
                "judge_run": manifest["run"],
                "source_run": manifest["source"]["run"],
                "items": manifest["items"],
                "model": manifest["judge"]["model_requested"],
                "prompt_version": manifest["judge"]["prompt_version"],
                "rubric": manifest["judge"]["rubric"]["version"],
                "cost_usd": manifest["totals"].get("cost_usd"),
                "questions": {
                    q.id: questions[q.id] for q in loaded.questions if q.id in questions
                },
            }
        )
    return {
        "agreement": None
        if scored is None
        else {
            "sample": scored["sample"],
            "judge_run": scored["judge_run"],
            "bar": scored["bar"],
            "questions": [
                {
                    k: q[k]
                    for k in (
                        "question",
                        "pairs",
                        "natural",
                        "seeded",
                        "deserve_no",
                        "agreement",
                        "kappa",
                        "caught",
                        "verdict",
                        "why",
                    )
                }
                for q in scored["questions"]
            ],
        },
        "per_run": per_run,
    }


def versions(run: Run) -> dict[str, Any]:
    stack = run.manifest.get("stack") or {}
    deployed = (stack.get("versions") or [{}])[0] if stack.get("versions") else {}
    return {
        "run": run.run_id,
        "mode": run.manifest["mode"],
        "system": run.manifest.get("system"),
        "started_at": run.manifest["started_at"],
        "code": run.manifest.get("code"),
        "environment": stack.get("environment"),
        "app": deployed.get("app"),
        "policy": (run.manifest.get("oracle") or {}).get("policy"),
        "prompts": deployed.get("prompts"),
        "snapshot": (stack.get("stamp") or {}).get("snapshot")
        or (run.manifest.get("versions") or {}).get("snapshot"),
        "grader": run.manifest.get("grader"),
        "models": [
            {
                k: m.get(k)
                for k in (
                    "node",
                    "purpose",
                    "model_requested",
                    "model_returned",
                    "calls",
                )
            }
            for m in run.manifest.get("models") or []
        ],
        "parallelism": run.manifest.get("parallelism"),
        "cost_usd": run.cost_usd,
        "unpriced_calls": run.unpriced_calls,
        "cases": len(run.graded),
        "passed": sum(bool(g["passed"]) for _, g in run.graded),
    }


def efficiency(run: Run) -> dict[str, Any]:
    """
    The run's M-05 as its summary states it, the cost filled from the priced calls when the summary left it undefined.
    """
    m5 = dict(run.summary["metrics"]["M-05"])
    cost = dict(m5["cost_usd"])
    if cost.get("models") == metrics.NOT_DEFINED and run.cost_usd is not None:
        tried = sum(metrics.attempted(g) for _, g in run.graded)
        done = sum(
            metrics.resolved(c, g) for c, g in run.graded if metrics.conversation(c)
        )
        cost["models"] = run.cost_usd
        cost["per_attempted_case"] = (
            round(run.cost_usd / tried, 6) if tried else metrics.NOT_DEFINED
        )
        cost["per_resolution"] = (
            round(run.cost_usd / done, 6) if done else metrics.NOT_DEFINED
        )
    cost["unpriced_calls"] = run.unpriced_calls
    m5["cost_usd"] = cost
    return m5


def mix(set_manifest: Mapping[str, Any]) -> dict[str, Any]:
    by_group: dict[str, Counter[str]] = {}
    for row in set_manifest["counts"]:
        by_group.setdefault(row["group"], Counter())[row["language"]] += row["cases"]
    return {
        "set": set_manifest["set"],
        "cases": set_manifest["cases"],
        "seed": set_manifest["seed"],
        "versions": set_manifest["versions"],
        "by_group": {g: dict(sorted(c.items())) for g, c in sorted(by_group.items())},
        "borrowed": sum(r["cases"] for r in set_manifest.get("borrowed", [])),
        "shared": sum(r["cases"] for r in set_manifest.get("shared", [])),
        "short": set_manifest.get("short", []),
    }


def build(
    runs: Sequence[Run],
    set_manifest: Mapping[str, Any],
    customers: Mapping[str, Profile],
    entries: Sequence[Mapping[str, Any]],
    baseline: Run | None = None,
    judged: Sequence[Path] = (),
    agreement: Path | None = None,
    loaded: rubric.Rubric | None = None,
    now: datetime | None = None,
    labels: Path | None = None,
) -> dict[str, Any]:
    if not runs:
        raise ReportError("no run to report")
    if len({r.set_name for r in runs}) != 1:
        raise ReportError("the runs reported together play one set")
    if baseline is not None and baseline.set_name != runs[0].set_name:
        raise ReportError("the baseline plays the runs' set")
    graded = [r.graded for r in runs]
    sides = sorted({c.get("side", "development") for run in graded for c, _ in run})

    def profile(case: Mapping[str, Any], field: str) -> list[str]:
        found = customers.get(case["customer_id"])
        if found is None:
            return ["unknown"]
        value = getattr(found, field)
        return [
            str(value)
            if not isinstance(value, bool)
            else ("updated" if value else "not updated")
        ]

    def side_of(case: Mapping[str, Any]) -> list[str]:
        return ["borrowed" if case.get("phrasing") != case.get("side") else "own"]

    return {
        "generated_at": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "measurement": "offline",
        "side": sides[0] if len(sides) == 1 else "mixed",
        "set": mix(set_manifest),
        "runs": [versions(r) for r in runs],
        "baseline": None if baseline is None else versions(baseline),
        "per_run": [
            {
                "run": r.run_id,
                **{
                    k: r.summary["metrics"][k] for k in ("M-01", "M-02", "M-03", "M-04")
                },
                "M-05": efficiency(r),
            }
            for r in runs
        ],
        "repeated": metrics.repeated(graded),
        "by_language": sliced(graded, lambda c: [c["language"]]),
        "by_segment": sliced(graded, lambda c: profile(c, "segment")),
        "by_country": sliced(graded, lambda c: profile(c, "country")),
        "by_group": sliced(graded, lambda c: [c["group"]]),
        "by_rule": sliced(graded, lambda c: list(c["expected"].get("rules", []))),
        "by_source": sliced(graded, lambda c: [c["source"]]),
        "by_phrasing": sliced(graded, side_of),
        "by_cards_updated": sliced(graded, lambda c: profile(c, "updated_after_as_of")),
        "failures": failures(runs, entries),
        "hypothesis": hypothesis(runs),
        "against_baseline": None if baseline is None else paired(runs[0], baseline),
        "judge": None
        if not judged and agreement is None
        else judge_section(judged, agreement, loaded or rubric.load()),
        "label_quality": None if labels is None else label_quality(labels),
        "suppression": {"under": MIN_CELL, "marked": SUPPRESSED},
    }


def label_quality(agreement_path: Path) -> dict[str, Any]:
    """
    The relabel's agreement (labels.py), as the report states label quality (EVL-08): counts and rates alone.
    """
    scored = json.loads(agreement_path.read_text(encoding="utf-8"))
    return {
        "sample": scored["sample"],
        "rows": scored["rows"],
        "exact": scored["exact"],
        "kappa_first_label": scored["kappa_first_label"],
        "by_author": scored["by_author"],
        "by_side": scored["by_side"],
        "disagreements": len(scored["disagreements"]),
        "settled": sum(d.get("settled") is not None for d in scored["disagreements"]),
    }


def _pct(value: Any) -> str:
    return f"{100 * value:.1f}%" if isinstance(value, float) else str(value)


def _rate(found: Mapping[str, Any]) -> str:
    if found.get("rate") == SUPPRESSED:
        return f"suppressed ({found['cases']})"
    if not isinstance(found.get("rate"), float):
        return str(found.get("rate", metrics.NOT_DEFINED))
    low, high = found["interval"]
    text = f"{_pct(found['rate'])} ({found['count']} of {found['cases']}; {_pct(low)} to {_pct(high)})"
    spread = found.get("spread")
    if isinstance(spread, list) and len(spread) == 2 and spread[0] != spread[1]:
        text += f", runs {_pct(spread[0])} to {_pct(spread[1])}"
    return text


def _spread(values: Any) -> str:
    if not isinstance(values, list) or not all(isinstance(v, float) for v in values):
        return metrics.NOT_DEFINED
    return f"{_pct(min(values))} to {_pct(max(values))}"


def _p(latency: Any, part: str) -> str:
    if not isinstance(latency, dict) or part not in latency:
        return metrics.NOT_DEFINED
    return f"{latency[part]['p50']} / {latency[part]['p95']}"


def _slice_table(
    title: str, found: Mapping[str, Mapping[str, Any]], what: str
) -> list[str]:
    lines = [
        f"### {title}",
        "",
        f"{what}",
        "",
        "| Value | Cases | M-01 | M-03 | M-04 |",
        "|---|---|---|---|---|",
    ]
    for value, row in found.items():
        lines.append(
            f"| {value} | {row['cases']} | {_rate(row['M-01'])} | {_rate(row['M-03'])} | {_rate(row['M-04'])} |"
        )
    return [*lines, ""]


def page(found: Mapping[str, Any]) -> str:
    """
    The page beside the run index, in plain words: every number an offline measurement on our cases.
    """
    runs = found["runs"]
    lines = [
        "# Results",
        "",
        "Generated by `make eval-report` from the runs' stored grades; do not edit. Every number here is an offline",
        "measurement on our own cases (EVL-13), never a production figure: the cases are drawn from the "
        + ("held-out fifth" if found["side"] == "held_out" else f"{found['side']} side")
        + " of the snapshot's customers, played as the runs say, and graded from the stored evidence",
        "([ADR-0005](../adr/0005-offline-scenario-evaluation.md), Reporting). A rate comes with its count, its sample",
        "size, and a 95% Wilson interval; over repeated runs it is the mean count over the runs at the slice's size,",
        f"with the runs' lowest and highest rates beside it. A slice under {found['suppression']['under']} cases is",
        "suppressed (SEC-03). The analysis of these numbers is in the [report](report.md).",
        "",
        f"Generated {found['generated_at']}.",
        "",
        "## What was run",
        "",
        "| Run | Mode | Environment | App | Policy | Grader | Parallel | Cases | Passed | Cost (USD) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in [*runs, *([found["baseline"]] if found["baseline"] else [])]:
        cost = r["cost_usd"]
        lines.append(
            f"| {r['run']} | {r['mode']} ({r['system']}) | {r['environment']} | {(r['app'] or '')[:7]} | "
            f"{r['policy']} | {r['grader']} | {r['parallelism']} | {r['cases']} | {r['passed']} | "
            f"{'' if cost is None else f'{cost:.2f}'} |"
        )
    first = runs[0]
    prompts = first.get("prompts") or {}
    lines += [
        "",
        f"Code at `{(first['code'] or {}).get('commit', '')[:7]}`"
        + (", clean tree" if (first["code"] or {}).get("clean") else ", tree not clean")
        + f"; snapshot `{first['snapshot']}`"
        + (
            "; prompts " + ", ".join(f"`{k}` {v}" for k, v in prompts.items())
            if prompts
            else ""
        )
        + ".",
        "",
        "Models, per node (the first run): "
        + "; ".join(
            f"{m['node']} {m['purpose']} {m['model_requested']} ({m['calls']} calls)"
            for m in first["models"]
        )
        + ".",
        "",
        "## The case mix",
        "",
        f"Set `{found['set']['set']}`: {found['set']['cases']} cases, seed {found['set']['seed']}; "
        f"{found['set']['borrowed']} borrow development phrasing where no held-out family fits, "
        f"{found['set']['shared']} share a customer across the two languages.",
        "",
        "| Group | es | pt |",
        "|---|---|---|",
    ]
    for group, counts in found["set"]["by_group"].items():
        lines.append(f"| {group} | {counts.get('es', 0)} | {counts.get('pt', 0)} |")
    if found["set"]["short"]:
        lines += [
            "",
            "Short of the plan: "
            + "; ".join(
                f"{s['situation']} {s['language']} {s['drawn']} of {s['wanted']}"
                for s in found["set"]["short"]
            )
            + ".",
        ]
    lines += [
        "",
        "## The metrics, per run",
        "",
        "| Run | M-01 resolved | attempted | M-02 no transfer | M-03 right | missed | unnecessary | M-04 unsafe |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in found["per_run"]:
        m1, m2, m3, m4 = r["M-01"], r["M-02"], r["M-03"], r["M-04"]
        lines.append(
            f"| {r['run']} | {_rate(m1)} | {_rate(m1['attempted'])} | {_rate(m2)} | {_rate(m3)} | "
            f"{_rate(m3['missed'])} | {_rate(m3['unnecessary'])} | {_rate(m4)} |"
        )
    rep = found["repeated"]
    lines += [
        "",
        f"Over {rep['runs']} run(s) on {rep['cases']} cases played every time: M-01 passes in every run for "
        f"{_rate(rep['M-01']['pass_all'])}, runs {_spread(rep['M-01']['spread'])}; M-03 for "
        f"{_rate(rep['M-03']['pass_all'])}, runs {_spread(rep['M-03']['spread'])}; M-04 in cases {_rate(rep['M-04'])}"
        + (
            f", bound at zero {rep['M-04']['bound']}"
            if rep["M-04"]["bound"] != metrics.NOT_DEFINED
            else ""
        )
        + ".",
        "",
        "## Efficiency (M-05), per run",
        "",
        "| Run | Turns p50 / p95 (ms) | First turns p50 / p95 | Cases p50 / p95 | Cost per attempted case | Cost per resolution | Turns per case | Calls per case |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in found["per_run"]:
        m5 = r["M-05"]
        lat = m5["latency_ms"]
        cost = m5["cost_usd"]
        lines.append(
            f"| {r['run']} | {_p(lat, 'later')} | {_p(lat, 'first')} | {_p(lat, 'cases')} | {cost['per_attempted_case']} | "
            f"{cost['per_resolution']} | {m5['per_case']['turns']} | {m5['per_case']['model_calls']} |"
        )
    unpriced = [
        f"`{r['run']}` {r['M-05']['cost_usd']['unpriced_calls']}"
        for r in found["per_run"]
        if r["M-05"]["cost_usd"].get("unpriced_calls")
    ]
    lines += [
        "",
        "Latency is measured by the harness from the send to the turn's last event; the first turn of a case opens",
        "its runtime session, so it is shown apart. Cost is the models' tokens at list price on the run date; AWS",
        'charges are not estimated here ("not defined").'
        + (
            " Model calls without a price (a failed attempt the harness retried carries no usage) are left out of"
            f" the sum: {', '.join(unpriced)}."
            if unpriced
            else ""
        ),
        "",
        "## By language and by segment",
        "",
    ]
    lines += [
        "In every slice M-01's denominator is each case of the slice, as the definition reads it, so a slice the",
        "oracle hands off entirely reads zero there and is judged by M-03 instead.",
        "",
    ]
    lines += _slice_table(
        "By language", found["by_language"], "The case's language (EVL-12)."
    )
    lines += _slice_table(
        "By segment",
        found["by_segment"],
        "The customer's segment in the snapshot (EVL-12).",
    )
    lines += _slice_table(
        "By country", found["by_country"], "The customer's country in the snapshot."
    )
    lines += ["## By group, rule, source, and phrasing", ""]
    lines += _slice_table("By group", found["by_group"], "The situation's group.")
    lines += _slice_table(
        "By rule",
        found["by_rule"],
        "A case counts under every rule its oracle cites, so the rows overlap.",
    )
    lines += _slice_table(
        "By source",
        found["by_source"],
        "Natural cases read the snapshot as it is; built ones add a fixture; harness ones add a fault plan or are decided before the graph.",
    )
    lines += _slice_table(
        "By phrasing",
        found["by_phrasing"],
        "Borrowed cases use development phrasing where no held-out family fits.",
    )
    lines += _slice_table(
        "By cards updated after the as-of instant",
        found["by_cards_updated"],
        "Whether any card of the customer is flagged as updated after the snapshot's as-of instant (ADR-0004).",
    )
    hyp = found["hypothesis"]
    lines += [
        "## Unsafe outcomes (M-04) and the hypothesis",
        "",
        f"Stated before the first run (CTL-04): disclosures and unauthorized actions stay at zero. Over {hyp['runs']} run(s) and "
        f"{hyp['cases']} cases, counting a case once if any run showed the kind: "
        + "; ".join(f"{k} {_rate(v)}" for k, v in hyp["by_kind"].items())
        + f". The hypothesis {hyp['verdict']}"
        + (
            f"; zero in {hyp['cases']} cases bounds the rate under {_pct(hyp['bound_at_zero'])} (the rule of three)"
            if hyp["verdict"] == "held"
            else ""
        )
        + ".",
        "",
        "## Failures, counted and classified",
        "",
        "| Run | Failed | Cases | By class | By group |",
        "|---|---|---|---|---|",
    ]
    for r in found["failures"]["per_run"]:
        lines.append(
            f"| {r['run']} | {r['failed']} | {r['cases']} | "
            + ", ".join(f"{k} {v}" for k, v in r["by_class"].items())
            + " | "
            + ", ".join(f"{k} {v}" for k, v in r["by_group"].items())
            + " |"
        )
    inc = found["failures"]["in_cases"]
    lines += [
        "",
        f"In cases, {inc['failed_in_any_run']} failed in at least one run: "
        + ", ".join(f"{k} {v}" for k, v in inc["by_class"].items())
        + ". A class is the first that fits: error (not played), unsafe, diverged (the router or the flow left the",
        "oracle's path), language, fact (a fact missing or a figure no fact holds), handoff (its payload), other.",
        "",
        "Findings matched by a disagreement entry: "
        + (
            ", ".join(
                f"[{k}](disagreements.md#{k.lower()}) {v}"
                for k, v in found["failures"]["by_entry"].items()
            )
            or "none"
        )
        + ". Findings no entry matches, by situation and check: "
        + (
            ", ".join(f"{k} {v}" for k, v in found["failures"]["unmatched"].items())
            or "none"
        )
        + ".",
        "",
    ]
    against = found["against_baseline"]
    if against:
        d = against["M-01"]["difference"]
        lines += [
            "## Against the baseline",
            "",
            f"The deterministic baseline ([ADR-0005](../adr/0005-offline-scenario-evaluation.md), Baselines) on the "
            f"{against['common_cases']} conversation cases both it and the system's first run played (EVL-01); access and "
            "expired-session cases are decided by the deployed stack and count for both.",
            "",
            "| Metric | System | Baseline |",
            "|---|---|---|",
            f"| M-01 | {_rate(against['M-01']['system'])} | {_rate(against['M-01']['baseline'])} |",
            f"| M-03 | {_rate(against['M-03']['system'])} | {_rate(against['M-03']['baseline'])} |",
            f"| M-04 | {_rate(against['M-04']['system'])} | {_rate(against['M-04']['baseline'])} |",
            "",
            (
                f"Paired difference in M-01: {_pct(d['value'])} ({_pct(d['interval'][0])} to {_pct(d['interval'][1])}), "
                f"from {d['only_system']} cases only the system resolved and {d['only_baseline']} only the baseline did."
                if isinstance(d.get("value"), float)
                else "Paired difference in M-01: not defined."
            ),
            "",
        ]
    quality = found.get("label_quality")
    if quality:
        exact, kappa = quality["exact"], quality["kappa_first_label"]
        lines += [
            "## Label quality",
            "",
            f"{quality['rows']} of the families' messages relabeled by hand, blind to their labels (DML-08, EVL-08; "
            f"sample `{quality['sample']}`): the label set agreed on {exact['agreed']} ({_pct(exact['value'])}, "
            f"{_pct(exact['low'])} to {_pct(exact['high'])}); kappa on the first label {kappa['value']} "
            f"({kappa['low']} to {kappa['high']}). By author: "
            + ", ".join(
                f"{k} {v['agreed']} of {v['rows']}"
                for k, v in quality["by_author"].items()
            )
            + "; by side: "
            + ", ".join(
                f"{k} {v['agreed']} of {v['rows']}"
                for k, v in quality["by_side"].items()
            )
            + f". {quality['disagreements']} disagreements, {quality['settled']} settled by the policy's text.",
            "",
        ]
    judge = found["judge"]
    if judge:
        lines += ["## The judge", ""]
        agreement = judge["agreement"]
        if agreement:
            bar = agreement["bar"]
            lines += [
                f"Validated on blind sample `{agreement['sample']}` by judge run `{agreement['judge_run']}` (EVL-10): the bar is "
                f"{_pct(bar['agreement'])} agreement, kappa {bar['kappa']}, and {bar['deserve_no']} replies that deserve a no. "
                "A question under the bar is graded by hand, not by the judge.",
                "",
                "| Question | Pairs (natural + seeded) | Deserve a no | Agreement | Kappa | Caught (natural, seeded) | Verdict |",
                "|---|---|---|---|---|---|---|",
            ]
            for q in agreement["questions"]:
                a, k = q["agreement"], q["kappa"]
                caught = q["caught"]
                lines.append(
                    f"| {q['question']} | {q['pairs']} ({q['natural']} + {q['seeded']}) | "
                    f"{q['deserve_no']['natural']} + {q['deserve_no']['seeded']} | "
                    f"{_pct(a['value']) if a['value'] is not None else 'none'} ({_pct(a['low'])} to {_pct(a['high'])}) | "
                    f"{k.get('value')} ({k.get('low')} to {k.get('high')}) | "
                    f"{caught['natural']['caught']} of {caught['natural']['failing']}, {caught['seeded']['caught']} of {caught['seeded']['failing']} | "
                    f"{q['verdict']}{' (' + '; '.join(q['why']) + ')' if q['why'] else ''} |"
                )
            lines.append("")
        for r in judge["per_run"]:
            cost = r["cost_usd"]
            lines += [
                f"Judge run `{r['judge_run']}` over run `{r['source_run']}`: {r['items']} replies, {r['model']}, prompt "
                f"{r['prompt_version']}, rubric {r['rubric']}, {'' if cost is None else f'{cost:.2f} USD'}.",
                "",
                "| Question | Replies | Pass rate | By hand | Answers | Used |",
                "|---|---|---|---|---|---|",
            ]
            for name, t in r["questions"].items():
                answers = ", ".join(f"{k} {v}" for k, v in t["answers"].items())
                lines.append(
                    f"| {name} | {t['items']} | {_rate(t['pass_rate'])} | {t['by_hand']} | {answers} | "
                    f"{'yes' if t['used'] else 'no (' + t['verdict'] + ')'} |"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write(
    found: Mapping[str, Any], out: Path = RESULTS, page_path: Path = PAGE
) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(found, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    page_path.write_text(page(found), encoding="utf-8")
    return out
