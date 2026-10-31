"""
The judge's agreement with blind hand grades: kappa and its linear-weighted form against values worked by hand, a Wilson
interval for agreement, a percentile bootstrap that resamples within strata and pairs every statistic on one resample,
and a report that counts natural and seeded replies apart and applies the bar fixed in the rubric (ADR-0005, Grading;
EVL-10).
"""

import json
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from faro.evaluation import __main__ as cli
from faro.evaluation import agreement, blind, intervals, judge, rubric
from faro.evaluation.judge import Judged

from .test_blind import pool

RUBRIC = rubric.load()


def test_cohen_s_kappa_matches_a_table_worked_by_hand() -> None:
    table = [("y", "y")] * 20 + [("y", "n")] * 5 + [("n", "y")] * 10 + [("n", "n")] * 15

    assert agreement.cohen(table) == pytest.approx(0.4)
    assert agreement.cohen([("y", "y"), ("n", "n")]) == pytest.approx(1.0)
    assert agreement.cohen([("y", "y")] * 4) is None
    assert agreement.cohen([]) is None


def test_weighted_kappa_matches_values_worked_by_hand() -> None:
    pairs = [(1, 1), (2, 3), (3, 3), (5, 4)]

    assert agreement.weighted(pairs, (1, 5)) == pytest.approx(1 - 0.125 / 0.375)
    assert agreement.weighted([(2, 2), (4, 4)], (1, 5)) == pytest.approx(1.0)
    assert agreement.weighted([(3, 3)] * 3, (1, 5)) is None


def test_wilson_matches_the_published_interval() -> None:
    low, high = intervals.wilson(9, 10) or (0.0, 0.0)

    assert (round(low, 3), round(high, 3)) == (0.596, 0.982)
    assert intervals.wilson(0, 0) is None
    every = intervals.wilson(10, 10) or (0.0, 0.0)
    assert every[0] == pytest.approx(0.7225, abs=1e-4)
    assert every[1] == pytest.approx(1.0)


def test_percentile_interpolates_between_ranks() -> None:
    assert intervals.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert intervals.percentile([5.0], 0.975) == 5.0


def test_the_bootstrap_is_seeded_stratified_and_paired() -> None:
    units = [("a", 1.0), ("a", 3.0), ("b", 10.0), ("b", 20.0), ("b", 30.0)]
    seen: list[list[tuple[str, float]]] = []

    def measure(drawn: Sequence[tuple[str, float]]) -> dict[str, float | None]:
        seen.append(list(drawn))
        mean = sum(v for _, v in drawn) / len(drawn)
        doubled = 2 * mean
        return {"mean": mean, "zero": doubled - 2 * mean}

    first = intervals.bootstrap(
        units, measure, resamples=200, seed=7, strata=lambda u: u[0]
    )
    again = intervals.bootstrap(
        units, measure, resamples=200, seed=7, strata=lambda u: u[0]
    )

    assert first == again
    assert first["mean"].value == pytest.approx(12.8)
    assert first["mean"].low is not None and first["mean"].high is not None
    assert first["mean"].low < 12.8 < first["mean"].high
    assert (first["zero"].low, first["zero"].high) == (0.0, 0.0)
    assert not first["zero"].excludes_zero()
    assert all(sum(u[0] == "a" for u in drawn) == 2 for drawn in seen)


def test_an_undefined_statistic_is_counted_out_of_its_resamples() -> None:
    found = intervals.bootstrap(
        [1, 2], lambda d: {"odd": None if sum(d) % 2 else 1.0}, resamples=100
    )

    assert 0 < found["odd"].defined < 100
    empty = intervals.bootstrap([], lambda d: {"x": None})
    assert empty["x"] == intervals.Estimate(None, None, None, 0)


def graded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, list[judge.Item]]:
    """
    A sample drawn from synthetic replies and a judge run kept over its items, with every answer passing.
    """
    found = pool()
    monkeypatch.setattr(judge, "run_items", lambda run: found)
    run = tmp_path / "run"
    run.mkdir()
    (run / "summary.json").write_text(
        json.dumps({"set": "regression", "mode": "in_process"}), encoding="utf-8"
    )
    sample = tmp_path / "sample"
    blind.sample(run, sample, RUBRIC, seed=11, per_language=10)
    items = judge.read_items(sample / "items.jsonl")
    judged = Judged()
    for item in items:
        judged.outcomes[item.item_id] = "ok"
        judged.answers[item.item_id] = {
            q.id: {
                "reason": "r",
                "answer": passing(q, item.language),
                "confidence": "high",
            }
            for q in RUBRIC.applicable(item.facts)
        }
    times = (datetime(2026, 10, 2, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC))
    judged_run = tmp_path / "judged"
    judge.keep(
        judged_run,
        items,
        RUBRIC,
        judged,
        judge.source(None, sample / "items.jsonl", items),
        {},
        times,
    )
    return sample, judged_run, items


def passing(q: rubric.Question, language: str) -> Any:
    return {"choice": q.passing.get(language), "yes_no": "yes", "score": 4}[q.type]


def fill(sample: Path, answer: Any) -> None:
    rows = blind.read_sheet(sample / "sheet.csv")
    key = {
        str(k["row"]): k
        for k in json.loads((sample / "key.json").read_text(encoding="utf-8"))
    }
    items = {i.item_id: i for i in judge.read_items(sample / "items.jsonl")}
    for row in rows:
        item = items[key[row["row"]]["item_id"]]
        for q in RUBRIC.questions:
            column = blind.header(q.id, RUBRIC)
            if row[column] != blind.NOT_ASKED:
                row[column] = str(answer(q, item, key[row["row"]]))
    blind.write_sheet(sample / "sheet.csv", rows)


def test_the_report_scores_each_question_and_applies_the_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample, judged_run, _ = graded(tmp_path, monkeypatch)
    rng = random.Random(0)

    # Four of the language seeds read as passing to the grader, which leaves that question short of the bar's count.
    missed = {f"seeded-language-{n:02d}" for n in range(7, 11)}

    def hand(q: rubric.Question, item: judge.Item, key: dict[str, Any]) -> Any:
        if key["seeded_for"] == q.id and item.item_id not in missed:
            return {"choice": "mixed", "yes_no": "no", "score": 1}[q.type]
        if q.id == "decision" and rng.random() < 0.5:
            return "no"
        return passing(q, item.language)

    fill(sample, hand)

    found = agreement.report(sample, judged_run, RUBRIC)

    assert found["validates"] is False
    assert found["bootstrap"] == {
        "resamples": 1000,
        "seed": intervals.SEED,
        "level": 0.95,
    }
    by = {q["question"]: q for q in found["questions"]}
    language = by["language"]
    assert language["seeded"] == 10 and language["deserve_no"] == {
        "natural": 0,
        "seeded": 6,
    }
    assert language["caught"]["seeded"] == {"failing": 6, "caught": 0}
    assert language["natural"] == 20 and language["pairs"] == 30
    assert language["verdict"] == "hand"
    assert "too few replies deserve a no" in language["why"]
    decision = by["decision"]
    assert decision["agreement"]["value"] < 0.9 and decision["verdict"] == "hand"
    assert decision["kappa"]["kind"] == "cohen"
    assert by["clear"]["kappa"]["kind"] == "weighted_linear"
    assert by["clear"]["verdict"] == "hand_or_drop"
    written = agreement.write(found, sample)
    assert json.loads(written.read_text(encoding="utf-8")) == found
    assert "Pasé su caso" not in written.read_text(encoding="utf-8")


def test_a_judge_that_agrees_on_enough_failing_replies_passes_the_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample, judged_run, items = graded(tmp_path, monkeypatch)
    failing = {i.item_id for i in items if i.seeded_for is None}
    failing = set(sorted(failing)[:12])
    kept = [
        json.loads(line)
        for line in (judged_run / "judgments.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    for j in kept:
        if j["item_id"] in failing:
            j["questions"]["no_verdict"]["answer"] = "no"
            j["questions"]["no_verdict"]["passes"] = False
    (judged_run / "judgments.jsonl").write_text(
        "".join(json.dumps(j) + "\n" for j in kept), encoding="utf-8"
    )

    def hand(q: rubric.Question, item: judge.Item, key: dict[str, Any]) -> Any:
        if q.id == "no_verdict" and item.item_id in failing:
            return "no"
        return passing(q, item.language)

    fill(sample, hand)

    by = {
        q["question"]: q
        for q in agreement.report(sample, judged_run, RUBRIC)["questions"]
    }

    found = by["no_verdict"]
    assert found["agreement"]["value"] == 1.0 and found["kappa"][
        "value"
    ] == pytest.approx(1.0)
    assert found["verdict"] == "judge" and found["why"] == []
    assert found["caught"]["natural"]["failing"] == found["caught"]["natural"]["caught"]


def test_an_ungraded_cell_or_another_judge_run_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample, judged_run, _ = graded(tmp_path, monkeypatch)

    with pytest.raises(agreement.AgreementError, match="no valid"):
        agreement.report(sample, judged_run, RUBRIC)
    fill(sample, lambda q, item, key: passing(q, item.language))
    manifest = json.loads((judged_run / "manifest.json").read_text(encoding="utf-8"))
    manifest["results"]["items.jsonl"] = "0" * 64
    (judged_run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(agreement.AgreementError, match="didn't judge"):
        agreement.report(sample, judged_run, RUBRIC)


def test_the_commands_draw_a_sample_and_score_a_judge_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sample, judged_run, _ = graded(tmp_path, monkeypatch)
    fill(sample, lambda q, item, key: passing(q, item.language))
    run = tmp_path / "run"
    out = tmp_path / "samples"

    drawn = cli.main(["judge-sample", "--run", str(run), "--out", str(out)])
    scored = cli.main(
        ["judge-agreement", "--sample", str(sample), "--judged", str(judged_run)]
    )

    printed = capsys.readouterr().out
    assert drawn == 0 and scored == 0
    assert "from the regression set played in_process" in printed
    assert "this sample checks the tooling" in printed
    assert all(f"{q.id}: " in printed for q in RUBRIC.questions)
    assert (sample / f"agreement-{judged_run.name}.json").is_file()
    below = ["judge-sample", "--run", str(run), "--out", str(out), "--seeded", "9"]
    assert cli.main(below) == 1
    assert "at least the bar's 10" in capsys.readouterr().err
    monkeypatch.setattr(judge, "run_items", lambda run: [])
    assert cli.main(["judge-sample", "--run", str(run), "--out", str(out)]) == 1
