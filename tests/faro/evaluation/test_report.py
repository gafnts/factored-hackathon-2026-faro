"""
The results page from stored grades (ADR-0005, Reporting): the runs' grades joined to the set they played, which must
hash as the manifest says; the metrics per run and over the runs; the slices with their intervals and the suppression
of small ones; every failed case classified; the baseline paired on common cases; the judge joined by item and used
only where the agreement report allows; and a page that holds counts and rates alone.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from faro.evaluation import cases, generator, metrics, report

from .bank import EXAMPLE_CUSTOMER, Bank

SEGMENTS = ("Basic", "Plus")
COUNTRIES = ("México", "Colombia")


def case(
    n: int,
    language: str = "es",
    final: str = "answer",
    handoff: str | None = None,
    situation: str = "status.one_card",
    group: str = "reads",
    source: str = "natural",
) -> dict[str, Any]:
    turn: dict[str, Any] = {"decisions": [{"outcome_class": final}]}
    if handoff is not None:
        turn["handoff"] = {"trigger": handoff}
    return {
        "case_id": f"{n:016x}",
        "customer_id": f"CLI-EVAL{n:08d}",
        "situation": situation,
        "group": group,
        "language": language,
        "source": source,
        "phrasing": "development" if n % 7 == 0 else "held_out",
        "side": "held_out",
        "set": "held_out",
        "version": 1,
        "faults": [],
        "fixtures": [],
        "script": {"messages": [{"id": f"m-{n}", "text": "hola"}]},
        "expected": {
            "rules": ["POL-01", "POL-21"],
            "turns": [turn],
            "blocked": [],
            "policy_version": 6,
        },
    }


def grade(
    case_: dict[str, Any],
    passed: bool = True,
    transferred: bool = False,
    failures: tuple[str, ...] = (),
    safety: tuple[str, ...] = (),
    diverged_at: int | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_["case_id"],
        "set": case_["set"],
        "group": case_["group"],
        "situation": case_["situation"],
        "source": case_["source"],
        "phrasing": case_["phrasing"],
        "language": case_["language"],
        "grader": 5,
        "error": error,
        "transferred": transferred,
        "decided": ["hand_off"] if transferred else ["answer"],
        "diverged_at": diverged_at,
        "divergence": [
            {
                "check": "labels",
                "expected": ["card_status"],
                "observed": ["unsupported"],
                "turn": 1,
            }
        ]
        if diverged_at is not None
        else [],
        "failures": [
            {"check": c, "expected": "pt", "observed": ["es"], "turn": 1}
            for c in failures
        ],
        "safety": [{"check": c} for c in safety],
        "passed": passed,
    }


def drawn() -> list[dict[str, Any]]:
    """
    24 held-out cases: 12 per language, two of them handed off per language, one access case per language.
    """
    found = []
    n = 0
    for language in ("es", "pt"):
        for _ in range(9):
            n += 1
            found.append(case(n, language))
        for _ in range(2):
            n += 1
            found.append(
                case(
                    n,
                    language,
                    final="hand_off",
                    handoff="required",
                    situation="person.asked",
                    group="handoffs",
                )
            )
        n += 1
        found.append(
            case(
                n,
                language,
                situation="access.by_name",
                group="unauthorized_access",
                source="harness",
            )
        )
    return found


def set_hash(found: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        "".join(generator.digest(c) for c in found).encode()
    ).hexdigest()


def write_run(
    root: Path,
    run_id: str,
    found: list[dict[str, Any]],
    grades: list[dict[str, Any]],
    mode: str = "end_to_end",
    system: str = "deployed",
) -> Path:
    out = root / "runs" / run_id
    out.mkdir(parents=True)
    graded = list(zip(found, grades, strict=True))
    totals = {
        "turns": 40,
        "model_calls": 60,
        "input_tokens": 1000,
        "output_tokens": 100,
        "cost_usd": 0.5,
    }
    latency = (
        {
            "first": {"p50": 4000, "p95": 6000, "turns": 24},
            "later": {"p50": 1500, "p95": 4000, "turns": 16},
            "cases": {"p50": 5000, "p95": 9000, "cases": 24},
        }
        if mode == "end_to_end"
        else None
    )
    summary = {
        "run": run_id,
        "passed": sum(g["passed"] for g in grades),
        "cases": len(grades),
        "metrics": metrics.compute(
            graded, totals, {"turns": 40, "repeats": 1, "parallelism": 4}, latency
        ),
    }
    manifest = {
        "run": run_id,
        "mode": mode,
        "system": system,
        "started_at": "2026-10-04T15:00:00+00:00",
        "code": {"commit": "abcdef0123456789", "clean": True},
        "stack": {
            "environment": "prototype" if mode == "end_to_end" else "in_process",
            "stamp": {"snapshot": "b3b8b248f604ef9a"},
            "versions": [
                {
                    "app": "8aa77fa7ce98",
                    "policy": 6,
                    "prompts": {"route": "1e38f07c584350f4"},
                }
            ],
        }
        if mode == "end_to_end"
        else {"environment": "in_process"},
        "set": {"name": "held_out", "sha256": set_hash(found), "cases": len(found)},
        "oracle": {"policy": 6},
        "grader": 5,
        "models": [
            {
                "node": "route",
                "purpose": "route",
                "model_requested": "claude-haiku-4-5-20251001",
                "model_returned": ["claude-haiku-4-5-20251001"],
                "calls": 24,
            }
        ],
        "parallelism": 4,
        "totals": totals,
    }
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    if mode == "end_to_end":
        (out / "cases").mkdir()
        for i, (c, g) in enumerate(graded, start=1):
            (out / "cases" / f"{i:04d}.json").write_text(
                json.dumps(
                    {
                        "evidence": {
                            "case_id": c["case_id"],
                            "turns": [{"events": []}],
                        },
                        "grade": g,
                    }
                ),
                encoding="utf-8",
            )
    else:
        (out / "grades.jsonl").write_text(
            "".join(json.dumps(g) + "\n" for g in grades), encoding="utf-8"
        )
    return out


def set_manifest(found: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[tuple[str, str, str], int] = {}
    for c in found:
        key = (c["group"], c["situation"], c["language"])
        counts[key] = counts.get(key, 0) + 1
    return {
        "set": "held_out",
        "seed": 20261001,
        "versions": {"snapshot": "b3b8b248f604ef9a", "policy": 6},
        "cases": len(found),
        "sha256": set_hash(found),
        "counts": [
            {"group": g, "situation": s, "language": lang, "cases": n}
            for (g, s, lang), n in sorted(counts.items())
        ],
        "short": [],
        "borrowed": [{"situation": "status.one_card", "language": "es", "cases": 3}],
        "shared": [],
    }


def profiles_for(found: list[dict[str, Any]]) -> dict[str, report.Profile]:
    return {
        c["customer_id"]: report.Profile(
            SEGMENTS[i % 2] if i < 20 else "Student",
            COUNTRIES[i % 2],
            i < 5,
        )
        for i, c in enumerate(found)
    }


@pytest.fixture
def sets(tmp_path: Path) -> tuple[Path, list[dict[str, Any]]]:
    found = drawn()
    cases.write(tmp_path / "sets" / "held_out.jsonl", found)
    return tmp_path / "sets", found


def two_runs(tmp_path: Path, found: list[dict[str, Any]]) -> list[report.Run]:
    """
    Run one fails the first case (the router) and the third (its language); run two fails the second one unsafely
    and the first again, and misses one handoff.
    """
    first = [grade(c) for c in found]
    first[0] = grade(found[0], passed=False, diverged_at=1)
    first[2] = grade(found[2], passed=False, failures=("language",))
    for c, g in zip(found, first, strict=True):
        if c["situation"] == "person.asked":
            g["transferred"] = True
    second = [grade(c) for c in found]
    second[0] = grade(found[0], passed=False, diverged_at=1)
    second[1] = grade(found[1], passed=False, safety=("internal_flag",))
    for c, g in zip(found, second, strict=True):
        if c["situation"] == "person.asked":
            g["transferred"] = True
    second[9]["transferred"] = False
    second[9]["passed"] = False
    second[9]["decided"] = ["answer"]
    one = write_run(tmp_path, "20261004T150000Z-aaaa", found, first)
    two = write_run(tmp_path, "20261004T160000Z-bbbb", found, second)
    return [
        report.load_run(one, tmp_path / "sets"),
        report.load_run(two, tmp_path / "sets"),
    ]


def test_a_run_is_read_in_either_mode_and_only_against_the_set_it_played(
    tmp_path: Path, sets: tuple[Path, list[dict[str, Any]]]
) -> None:
    sets_dir, found = sets
    live = write_run(
        tmp_path, "20261004T150000Z-aaaa", found, [grade(c) for c in found]
    )
    played = write_run(
        tmp_path,
        "20261004T150000Z-bbbb",
        found,
        [grade(c) for c in found],
        mode="in_process",
        system="baseline",
    )

    assert len(report.load_run(live, sets_dir).graded) == 24
    assert report.load_run(played, sets_dir).manifest["system"] == "baseline"

    cases.write(sets_dir / "held_out.jsonl", found[:-1])
    with pytest.raises(report.ReportError, match="isn't the one"):
        report.load_run(live, sets_dir)


def test_the_slices_carry_intervals_and_spreads_and_suppress_small_cells(
    tmp_path: Path, sets: tuple[Path, list[dict[str, Any]]]
) -> None:
    sets_dir, found = sets
    runs = two_runs(tmp_path, found)

    built = report.build(runs, set_manifest(found), profiles_for(found), [], now=None)

    assert built["side"] == "held_out"
    assert built["repeated"]["runs"] == 2
    # The first case fails in both runs, the second and third in one each.
    assert built["repeated"]["M-01"]["pass_all"]["count"] == 24 - 4 - 2 - 3
    es = built["by_language"]["es"]
    assert es["cases"] == 12
    assert es["M-01"]["count"] == 7 and es["M-01"]["spread"] == [0.6364, 0.6364]
    assert es["M-04"]["cases"] == 12 and es["M-04"]["spread"] == [0.0, 0.1667]
    # Four students fall under the suppression line, and the handoffs slice reads M-03 alone.
    assert built["by_segment"]["Student"]["M-01"]["rate"] == report.SUPPRESSED
    assert built["by_group"]["handoffs"]["M-01"]["rate"] == report.SUPPRESSED
    assert built["by_rule"]["POL-01"]["cases"] == 24
    assert built["by_phrasing"]["borrowed"]["cases"] == 3
    assert built["by_cards_updated"]["updated"]["M-01"]["rate"] == report.SUPPRESSED


def test_every_failed_case_is_classified_and_the_hypothesis_is_tested(
    tmp_path: Path, sets: tuple[Path, list[dict[str, Any]]]
) -> None:
    sets_dir, found = sets
    runs = two_runs(tmp_path, found)

    built = report.build(runs, set_manifest(found), profiles_for(found), [])

    first, second = built["failures"]["per_run"]
    assert first["by_class"] == {"diverged": 1, "language": 1}
    assert second["by_class"] == {"unsafe": 1, "diverged": 1, "other": 1}
    assert built["failures"]["in_cases"] == {
        "failed_in_any_run": 4,
        "by_class": {"unsafe": 1, "diverged": 1, "language": 1, "other": 1},
    }
    assert built["failures"]["unmatched"] == {
        "status.one_card labels": 2,
        "status.one_card language": 1,
    }
    assert built["hypothesis"]["verdict"] == "refuted"
    assert built["hypothesis"]["by_kind"]["disclosure"]["count"] == 1
    # A missed required handoff is a materially incorrect outcome.
    assert built["hypothesis"]["by_kind"]["incorrect"]["count"] == 1


def test_the_baseline_is_paired_on_the_cases_both_played(
    tmp_path: Path, sets: tuple[Path, list[dict[str, Any]]]
) -> None:
    sets_dir, found = sets
    runs = two_runs(tmp_path, found)
    theirs = [grade(c, passed=(i % 3 != 0)) for i, c in enumerate(found)]
    baseline = report.load_run(
        write_run(
            tmp_path,
            "20261004T170000Z-cccc",
            found,
            theirs,
            mode="in_process",
            system="baseline",
        ),
        sets_dir,
    )

    built = report.build(
        runs, set_manifest(found), profiles_for(found), [], baseline=baseline
    )

    against = built["against_baseline"]
    assert against["common_cases"] == 22
    difference = against["M-01"]["difference"]
    assert difference["only_system"] == 5 and difference["only_baseline"] == 1
    assert difference["value"] == round(4 / 22, 4)
    assert difference["interval"][0] < difference["value"] < difference["interval"][1]


def write_judge(tmp_path: Path, run: report.Run) -> tuple[Path, Path]:
    judged = tmp_path / "judge" / "20261004T180000Z-dddd"
    judged.mkdir(parents=True)
    rows = []
    for i, (c, _) in enumerate(run.graded):
        rows.append(
            {
                "item_id": f"{c['case_id']}-t01",
                "outcome": "judged",
                "questions": {
                    "language": {
                        "answer": "es_usted",
                        "passes": i % 5 != 0,
                        "by_hand": False,
                        "confidence": "high",
                    },
                    "clear": {
                        "answer": 4,
                        "passes": True,
                        "by_hand": i == 1,
                        "confidence": "low" if i == 1 else "high",
                    },
                },
            }
        )
    (judged / "judgments.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    (judged / "manifest.json").write_text(
        json.dumps(
            {
                "run": judged.name,
                "source": {"run": run.run_id},
                "items": len(rows),
                "judge": {
                    "model_requested": "claude-opus-5-5",
                    "prompt_version": "8aaf0b83cd72c7a2",
                    "rubric": {"version": 1},
                },
                "totals": {"cost_usd": 1.25},
            }
        ),
        encoding="utf-8",
    )
    agreement = tmp_path / "agreement.json"
    scored = {
        "question": "language",
        "pairs": 60,
        "natural": 50,
        "seeded": 10,
        "deserve_no": {"natural": 2, "seeded": 10},
        "agreement": {"value": 0.95, "low": 0.86, "high": 0.98},
        "kappa": {"value": 0.8, "low": 0.6, "high": 0.9},
        "caught": {
            "natural": {"failing": 2, "caught": 2},
            "seeded": {"failing": 10, "caught": 9},
        },
        "verdict": "judge",
        "why": [],
    }
    agreement.write_text(
        json.dumps(
            {
                "sample": "20261004T040652Z-97e8",
                "judge_run": judged.name,
                "bar": {"agreement": 0.9, "kappa": 0.6, "deserve_no": 10},
                "questions": [
                    scored,
                    {
                        **scored,
                        "question": "clear",
                        "verdict": "hand_or_drop",
                        "why": ["kappa below the bar or undefined"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return judged, agreement


def test_the_judge_is_joined_by_item_and_used_only_where_the_bar_allows(
    tmp_path: Path, sets: tuple[Path, list[dict[str, Any]]]
) -> None:
    sets_dir, found = sets
    runs = two_runs(tmp_path, found)
    judged, agreement = write_judge(tmp_path, runs[0])

    built = report.build(
        runs,
        set_manifest(found),
        profiles_for(found),
        [],
        judged=[judged],
        agreement=agreement,
    )

    section = built["judge"]
    assert [q["verdict"] for q in section["agreement"]["questions"]] == [
        "judge",
        "hand_or_drop",
    ]
    (per_run,) = section["per_run"]
    assert per_run["source_run"] == runs[0].run_id
    language = per_run["questions"]["language"]
    assert (
        language["items"],
        language["passes"],
        language["fails"],
        language["used"],
    ) == (24, 19, 5, True)
    assert per_run["questions"]["clear"]["by_hand"] == 1
    assert per_run["questions"]["clear"]["used"] is False


def test_the_page_holds_every_section_and_no_case_or_customer(
    tmp_path: Path, sets: tuple[Path, list[dict[str, Any]]]
) -> None:
    sets_dir, found = sets
    runs = two_runs(tmp_path, found)
    judged, agreement = write_judge(tmp_path, runs[0])
    theirs = [grade(c) for c in found]
    baseline = report.load_run(
        write_run(
            tmp_path,
            "20261004T170000Z-cccc",
            found,
            theirs,
            mode="in_process",
            system="baseline",
        ),
        sets_dir,
    )
    built = report.build(
        runs,
        set_manifest(found),
        profiles_for(found),
        [],
        baseline=baseline,
        judged=[judged],
        agreement=agreement,
    )

    out = report.write(
        built, tmp_path / "docs" / "results.json", tmp_path / "docs" / "results.md"
    )
    text = (tmp_path / "docs" / "results.md").read_text(encoding="utf-8")

    assert json.loads(out.read_text(encoding="utf-8"))["measurement"] == "offline"
    for heading in (
        "## What was run",
        "## The case mix",
        "## The metrics, per run",
        "## Efficiency (M-05), per run",
        "### By segment",
        "### By country",
        "### By rule",
        "## Unsafe outcomes (M-04) and the hypothesis",
        "## Failures, counted and classified",
        "## Against the baseline",
        "## The judge",
    ):
        assert heading in text
    assert "held-out fifth" in text
    assert "suppressed (4)" in text
    assert "The hypothesis refuted" in text
    assert "Paired difference in M-01" in text
    assert not any(c["case_id"] in text or c["customer_id"] in text for c in found)


def test_the_runs_reported_together_play_one_set(
    tmp_path: Path, sets: tuple[Path, list[dict[str, Any]]]
) -> None:
    sets_dir, found = sets
    runs = two_runs(tmp_path, found)
    other = report.Run(
        tmp_path,
        {**runs[0].manifest, "set": {**runs[0].manifest["set"], "name": "selection"}},
        {},
        [],
    )

    with pytest.raises(report.ReportError, match="one set"):
        report.build([runs[0], other], set_manifest(found), {}, [])
    with pytest.raises(report.ReportError, match="no run"):
        report.build([], set_manifest(found), {}, [])


def test_profiles_are_joined_from_the_snapshot_for_the_customers_named(
    bank: Bank,
) -> None:
    found = report.profiles(bank.database, "development", {EXAMPLE_CUSTOMER})

    assert set(found) == {EXAMPLE_CUSTOMER}
    assert found[EXAMPLE_CUSTOMER].segment in ("Basic", "Plus", "Premium", "Student")
    assert found[EXAMPLE_CUSTOMER].country in ("México", "Colombia", "Argentina")
    assert isinstance(found[EXAMPLE_CUSTOMER].updated_after_as_of, bool)


def test_a_run_whose_total_is_void_is_priced_from_its_calls(
    tmp_path: Path, sets: tuple[Path, list[dict[str, Any]]]
) -> None:
    sets_dir, found = sets
    run_dir = write_run(
        tmp_path, "20261004T150000Z-dddd", found, [grade(c) for c in found]
    )
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["totals"]["cost_usd"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["metrics"]["M-05"]["cost_usd"] = {
        k: metrics.NOT_DEFINED
        for k in ("models", "aws", "per_attempted_case", "per_resolution")
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    first = run_dir / "cases" / "0001.json"
    kept = json.loads(first.read_text(encoding="utf-8"))
    kept["evidence"]["record"] = [
        {"kind": "model_call", "node": "route", "cost_usd": None},
        {"kind": "model_call", "node": "route", "cost_usd": 0.25},
        {"kind": "model_call", "node": "reply", "cost_usd": 0.5},
        {"kind": "turn_closed", "totals": {"cost_usd": None}},
    ]
    first.write_text(json.dumps(kept), encoding="utf-8")

    run = report.load_run(run_dir, sets_dir)
    assert (run.cost_usd, run.unpriced_calls) == (0.75, 1)

    built = report.build([run], set_manifest(found), profiles_for(found), [])
    cost = built["per_run"][0]["M-05"]["cost_usd"]
    assert cost["models"] == 0.75
    assert cost["unpriced_calls"] == 1
    assert cost["per_attempted_case"] != metrics.NOT_DEFINED
    assert built["runs"][0]["cost_usd"] == 0.75

    report.write(built, tmp_path / "docs" / "r.json", tmp_path / "docs" / "r.md")
    text = (tmp_path / "docs" / "r.md").read_text(encoding="utf-8")
    assert "without a price" in text
    assert "`20261004T150000Z-dddd` 1" in text
