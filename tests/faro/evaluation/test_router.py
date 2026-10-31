"""
The router comparison reads one side of the family split, scores each candidate per language on the first request in
POL-05's order, pairs candidates over families resampled within groups, keeps tuning to grouped folds of the
development side, and runs the held-out side only from a clean tree and never in folds (ADR-0005, Baselines: the
router, as amended on 2026-10-02; DML-07, DML-09, DML-11, DML-12). No test calls a model.
"""

import asyncio
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from faro.agent.models import RouterOutput
from faro.evaluation import __main__ as cli
from faro.evaluation import families, model_commands, router
from faro.evaluation.router import Candidate, Message, Reading, Result

LOADED, ANSWERS = families.load(), families.load_answers()
HELD = families.held_out_ids(LOADED, ANSWERS)
DEVELOPMENT = router.messages(LOADED, HELD, "development")
LABELS = {m.text: m.labels for m in DEVELOPMENT}


def candidate(
    name: str, labels_of: Callable[[str], Sequence[str]], **fields: Any
) -> Candidate:
    async def read(text: str) -> Reading:
        labels = tuple(labels_of(text))
        return Reading(labels, bool(labels))

    return Candidate(name, read, **fields)


def truth(text: str) -> Sequence[str]:
    return LABELS.get(text, ())


PERFECT = candidate("perfect", truth)
SILENT = candidate("silent", lambda text: ())


def read(c: Candidate, found: Sequence[Message] = DEVELOPMENT) -> list[Result]:
    return asyncio.run(router.read_all(c, found))


def test_each_side_holds_only_its_own_families_with_slots_filled() -> None:
    held_out = router.messages(LOADED, HELD, "held_out")

    assert {m.family_id for m in DEVELOPMENT}.isdisjoint(HELD)
    assert {m.family_id for m in held_out} <= HELD
    assert len({m.family_id for m in DEVELOPMENT}) == 72
    assert len({m.family_id for m in held_out}) == 36
    assert {m.language for m in DEVELOPMENT} == {"es", "pt", "other"}
    assert not any("{" in m.text for m in DEVELOPMENT + held_out)
    assert all(m.has_request == bool(m.labels) for m in DEVELOPMENT)
    with pytest.raises(router.RouterError):
        router.messages(LOADED, HELD, "both")


def test_the_first_request_follows_pol_05_s_order() -> None:
    assert router.first(["recent_transactions", "block_card"]) == "block_card"
    assert router.first(["unsupported", "talk_to_human"]) == "talk_to_human"
    assert router.first([]) == router.NONE
    assert router.predicted(Reading(("card_status",), False)) == router.NONE


def test_a_perfect_reader_scores_full_marks_and_a_silent_one_only_the_gate() -> None:
    perfect = router.measures(read(PERFECT))
    silent = router.measures(read(SILENT))
    no_request = sum(not m.has_request for m in DEVELOPMENT) / len(DEVELOPMENT)

    for figure in (
        "macro_f1",
        "first_accuracy",
        "gate_accuracy",
        "multi_detected",
        "multi_exact",
    ):
        assert perfect[figure] == 1.0, figure
    assert perfect["false_multi"] == 0.0 and perfect["failed"] == 0.0
    assert perfect["cost_usd"] == 0.0 and perfect["brier"] is None
    assert all(perfect[f"recall.{label}"] == 1.0 for label in router.LABELS)
    assert silent["macro_f1"] == 0.0
    assert silent["gate_accuracy"] == pytest.approx(no_request)
    assert silent["first_accuracy"] == pytest.approx(no_request)
    assert router.measures([]) == {}


def test_the_confusion_matrix_counts_first_requests() -> None:
    found = router.confusion(read(SILENT))

    assert set(found) == {*router.LABELS, router.NONE}
    assert all(set(row) == {router.NONE} for row in found.values())
    assert sum(sum(row.values()) for row in found.values()) == len(DEVELOPMENT)
    perfect = router.confusion(read(PERFECT))
    assert all(set(row) == {expected} for expected, row in perfect.items())


def test_calibration_is_scored_only_where_a_candidate_gives_probabilities() -> None:
    sure = [
        Result(
            m, Reading(m.labels, m.has_request, {label: 1.0 for label in m.labels}), 1.0
        )
        for m in DEVELOPMENT[:40]
    ]
    unsure = [
        Result(
            m, Reading(m.labels, m.has_request, dict.fromkeys(router.LABELS, 0.5)), 1.0
        )
        for m in DEVELOPMENT[:40]
    ]

    assert router.calibration(sure) == {"brier": 0.0, "ece": 0.0}
    assert router.calibration(unsure)["brier"] == pytest.approx(0.25)
    assert router.calibration(read(PERFECT, DEVELOPMENT[:5])) == {
        "brier": None,
        "ece": None,
    }


def test_candidates_are_paired_over_the_same_families() -> None:
    copy = candidate("copy", truth)
    found = router.paired(
        {"silent": read(SILENT), "perfect": read(PERFECT), "copy": read(copy)},
        resamples=200,
    )

    better = found["perfect - silent.macro_f1"]
    assert (
        better.excludes_zero()
        and router.verdict(better, "perfect", "silent") == "perfect"
    )
    same = found["copy - perfect.macro_f1"]
    assert (same.low, same.high) == (0.0, 0.0)
    assert router.verdict(same, "copy", "perfect") == "tie"
    assert found["perfect.macro_f1"].value == 1.0
    assert found["silent.gate_accuracy"].low is not None


def test_a_language_with_one_family_per_group_decides_nothing() -> None:
    other = [m for m in DEVELOPMENT if m.language == "other"]
    eager = candidate("eager", lambda text: ["card_status"])

    found = router.by_language(
        {"perfect": read(PERFECT, other), "eager": read(eager, other)}, resamples=50
    )

    assert found["other"]["families"] == 1
    gate = found["other"]["differences"]["eager - perfect"]["gate_accuracy"]
    assert gate["low"] == gate["high"] < 0
    assert gate["beats"] == "too_few_families"


def test_routed_turns_a_router_in_code_into_a_free_candidate() -> None:
    def route(text: str) -> RouterOutput:
        return RouterOutput(
            language="es", requests=["card_status"], has_request=True, complaint=False
        )

    made = router.routed("keyword", route)
    reading = asyncio.run(made.read("¿Mi tarjeta?"))

    assert reading == Reading(("card_status",), True)
    assert made.fit is None and made.estimate is None
    assert router.estimate([made], DEVELOPMENT[:3]) == {
        "keyword": {"calls": 3, "cost_usd": 0.0}
    }


def test_folds_keep_each_family_whole_and_every_group_in_each_fold() -> None:
    found = router.folds(DEVELOPMENT, 5)
    groups = {m.family_id: m.group for m in DEVELOPMENT}

    assert len(found) == 5
    assert sorted(f for fold in found for f in fold) == sorted(groups)
    assert all({groups[f] for f in fold} == set(groups.values()) for fold in found)
    assert found == router.folds(DEVELOPMENT, 5)


def test_a_candidate_that_learns_is_fitted_without_the_fold_it_reads() -> None:
    seen: list[tuple[set[str], set[str]]] = []

    def fit(train: Sequence[Message]) -> Candidate:
        known = {m.text: m.labels for m in train}
        trained = {m.family_id for m in train}

        def labels_of(text: str) -> Sequence[str]:
            seen[-1][1].add(text)
            return known.get(text, ())

        seen.append((trained, set()))
        return candidate("learner", labels_of)

    learner = candidate("learner", truth, fit=fit)
    found = router.messages(LOADED, HELD, "development")

    reported = asyncio.run(
        router.cross_validate(
            [learner, PERFECT], found, {"perfect": read(PERFECT)}, 3, 4
        )
    )

    assert len(reported) == len(seen) == 3
    by_text = {m.text: m.family_id for m in found}
    for trained, texts in seen:
        assert not trained & {by_text[t] for t in texts}
    assert all(f["candidates"]["learner"]["macro_f1"] == 0.0 for f in reported)
    assert all(f["candidates"]["perfect"]["macro_f1"] == 1.0 for f in reported)
    summary = router.fold_summary(reported)
    assert summary["perfect"]["macro_f1"] == {"mean": 1.0, "low": 1.0, "high": 1.0}


def test_a_comparison_keeps_its_readings_errors_calls_and_report(
    tmp_path: Path,
) -> None:
    calls = [{"candidate": "perfect", "outcome": "ok"}]

    report = asyncio.run(
        router.compare(
            [SILENT, PERFECT],
            "development",
            tmp_path / "run",
            {"clean": False},
            calls,
            k=3,
            resamples=50,
        )
    )

    assert set(report["results"]) == {"readings.jsonl", "errors.jsonl", "calls.jsonl"}
    assert set(report["by_language"]) == {"es", "pt", "other"}
    spanish = report["by_language"]["es"]
    assert spanish["differences"]["perfect - silent"]["macro_f1"]["beats"] == "perfect"
    assert spanish["candidates"]["perfect"]["macro_f1"]["value"] == 1.0
    assert (
        report["bootstrap"]["resamples"] == 50
        and report["bootstrap"]["unit"] == "family"
    )
    assert report["folds"]["k"] == 3 and len(report["folds"]["per_fold"]) == 3
    assert report["totals"]["silent"]["calls"] == len(DEVELOPMENT)
    readings = (
        (tmp_path / "run" / "readings.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert len(readings) == 2 * len(DEVELOPMENT)
    kept = [
        json.loads(line)
        for line in (tmp_path / "run" / "errors.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {e["candidate"] for e in kept} == {"silent"}
    assert all(e["expected"] for e in kept)
    assert (
        json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))
        == report
    )


def test_the_held_out_side_runs_only_from_a_clean_tree_and_never_in_folds(
    tmp_path: Path,
) -> None:
    with pytest.raises(router.RouterError, match="clean tree"):
        asyncio.run(
            router.compare([PERFECT], "held_out", tmp_path / "a", {"clean": False})
        )
    with pytest.raises(router.RouterError, match="no fold"):
        asyncio.run(
            router.compare([PERFECT], "held_out", tmp_path / "b", {"clean": True}, k=3)
        )
    with pytest.raises(router.RouterError, match="once"):
        asyncio.run(
            router.compare([PERFECT, PERFECT], "development", tmp_path / "c", {})
        )

    report = asyncio.run(
        router.compare(
            [SILENT], "held_out", tmp_path / "d", {"clean": True}, resamples=20
        )
    )

    assert report["side"] == "held_out"
    assert report["families"]["families"] == 36


def test_the_command_prices_registered_candidates_without_a_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(
        model_commands.CANDIDATES, "silent", lambda env_file, calls: SILENT
    )

    assert cli.main(["router", "--candidate", "silent", "--estimate"]) == 0
    assert capsys.readouterr().out.startswith(f"silent: {len(DEVELOPMENT)} calls")
    with pytest.raises(SystemExit):
        cli.main(["router", "--candidate", "nobody", "--estimate"])


def test_the_command_runs_a_comparison_with_folds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(model_commands.CANDIDATES, "silent", lambda e, c: SILENT)
    monkeypatch.setitem(model_commands.CANDIDATES, "perfect", lambda e, c: PERFECT)
    chosen = ["--candidate", "silent", "--candidate", "perfect"]

    code = cli.main(["router", *chosen, "--folds", "3", "--out", str(tmp_path)])

    printed = capsys.readouterr().out
    assert code == 0
    assert "perfect - silent: macro_f1 perfect" in printed
    (kept,) = tmp_path.iterdir()
    report = json.loads((kept / "report.json").read_text(encoding="utf-8"))
    assert report["folds"]["k"] == 3
    held_out = ["router", *chosen, "--side", "held_out", "--folds", "3"]
    assert cli.main(held_out) == 1
