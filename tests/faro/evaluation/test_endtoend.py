"""
A set run end to end: only the set its manifest describes, narrowed as asked; its cases played in parallel and each
kept once as it finishes, locally and in the bucket, a case the stack failed outside a turn kept as the harness's
error; and the run's manifest and summary.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from moto import mock_aws

from faro.evaluation import (
    bronze,
    cases,
    deployed,
    endtoend,
    families,
    generator,
    grader,
    harness,
    player,
    users,
)
from faro.evaluation.client import Client
from faro.evaluation.deployed import StackError
from faro.evaluation.facts import contract_words

from .bank import Bank
from .test_deployed import written
from .test_harness import INVOKE, STOP, Served, Signing, items, models
from .test_users import CLIENT, POOL

pytestmark = pytest.mark.xdist_group("evaluation_bank")

LOADED, ANSWERS = families.load(), families.load_answers()


@pytest.fixture(scope="module")
def drawn(bank: Bank) -> tuple[generator.Drawn, dict[str, Any]]:
    held = families.held_out_ids(LOADED, ANSWERS)
    with bronze.connect(bank.database, "development") as con:
        drawing = generator.Generator(
            con, "development", LOADED, ANSWERS, held, contract_words(), reuse=True
        )
        found = drawing.draw("regression", 7)
        built, _ = bronze.stamp(con)
    return found, generator.manifest("regression", 7, found, {**built, "policy": 2})


def set_file(tmp_path: Path, found: generator.Drawn) -> Path:
    path = tmp_path / "regression.jsonl"
    cases.write(path, found.cases)
    return path


def test_only_the_set_its_manifest_describes_runs(
    tmp_path: Path, drawn: tuple[generator.Drawn, dict[str, Any]]
) -> None:
    found, manifest = drawn
    path = set_file(tmp_path, found)

    assert len(endtoend.drawn(path, manifest)) == len(found.cases)
    with pytest.raises(endtoend.RunError, match="manifest describes"):
        endtoend.drawn(path, {**manifest, "sha256": "0" * 64})


def test_a_set_is_narrowed_by_situation_language_and_count(
    drawn: tuple[generator.Drawn, dict[str, Any]],
) -> None:
    found, _ = drawn

    chosen = endtoend.selected(
        found.cases, ["block.reason_given", "status.one_card"], ["pt"], None
    )

    assert {(c["situation"], c["language"]) for c in chosen} == {
        ("block.reason_given", "pt"),
        ("status.one_card", "pt"),
    }
    assert len(endtoend.selected(found.cases, limit=3)) == 3


def test_a_stack_serving_another_snapshot_is_refused_before_anything_runs(
    tmp_path: Path, drawn: tuple[generator.Drawn, dict[str, Any]]
) -> None:
    found, manifest = drawn

    with pytest.raises(StackError, match="another snapshot"):
        endtoend.run(
            set_file(tmp_path, found),
            manifest,
            written(tmp_path),
            tmp_path / "runs",
            {"commit": "0" * 40, "clean": True},
            {"situations": [], "languages": [], "limit": None},
            1,
        )


@mock_aws
def test_a_result_is_kept_once_locally_and_in_the_bucket(tmp_path: Path) -> None:
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="evaluation")
    keeper = endtoend.Keeper(tmp_path / "run", "run", s3, "evaluation")

    keeper.keep("cases/0001.json", {"grade": {"passed": True}})

    local = (tmp_path / "run" / "cases" / "0001.json").read_bytes()
    stored = s3.get_object(Bucket="evaluation", Key="runs/run/cases/0001.json")
    assert stored["Body"].read() == local
    assert set(keeper.hashes) == {"cases/0001.json"}
    with pytest.raises(FileExistsError):
        keeper.keep("cases/0001.json", {"grade": {"passed": False}})


def play_on_its_own_stack(bank: Bank) -> Any:
    def play(case: dict[str, Any], name: str) -> dict[str, Any]:
        if case["situation"] == "person.asked":
            raise RuntimeError("the user pool refused")
        served = Served(player.Stack(items(bank, case), models(bank, case)))
        agui = Client(
            INVOKE, STOP, httpx.Client(transport=httpx.MockTransport(served.handle))
        )
        test_users = users.Users(Signing(), POOL, CLIENT)  # type: ignore[arg-type]
        return harness.play(case, name, test_users, agui, served)

    return play


def test_fault_cases_stay_out_of_the_turn_and_case_latency() -> None:
    def one(faults: list[dict[str, Any]], *at_ms: int) -> endtoend.Played:
        turns = [{"events": [{"at_ms": at}]} for at in at_ms]
        return {"faults": faults}, {"turns": turns}, {}

    fault = {"tool": "get_card", "failures": 3, "error": "timeout"}
    played = [one([], 900, 300), one([fault], 5, 4), one([], 1100)]

    found = endtoend.latency(played)

    assert (found["first"]["turns"], found["first"]["p50"]) == (2, 1000)
    assert found["later"] == {"turns": 1, "p50": 300}
    assert (found["cases"]["cases"], found["cases"]["p50"]) == (2, 1150)
    assert found["fault_cases_left_out"] == 1


def test_a_set_plays_in_parallel_and_each_case_is_kept_as_it_finishes(
    tmp_path: Path, bank: Bank, drawn: tuple[generator.Drawn, dict[str, Any]]
) -> None:
    found, manifest = drawn
    chosen = endtoend.selected(
        found.cases, ["block.reason_given", "status.one_card", "person.asked"], ["es"]
    )
    keeper = endtoend.Keeper(tmp_path / "run", "run", None, None)

    played = endtoend.play_all(chosen, "run", play_on_its_own_stack(bank), keeper, 2)

    assert [c["case_id"] for c, _, _ in played] == [c["case_id"] for c in chosen]
    kept = sorted(p.name for p in (tmp_path / "run" / "cases").iterdir())
    assert kept == [f"{n:04d}.json" for n in range(1, len(chosen) + 1)]
    by_situation = {c["situation"]: g for c, _, g in played}
    assert by_situation["person.asked"]["error"] == "harness: RuntimeError"
    assert by_situation["block.reason_given"]["passed"]
    assert by_situation["status.one_card"]["passed"]
    for n, (_, evidence, _) in enumerate(played, 1):
        assert evidence["user"] == users.username("run", n)

    summary = endtoend.summarize(played, 2)
    assert (summary["cases"], summary["passed"], summary["errors"]) == (3, 2, 1)
    assert summary["latency_ms"]["first"]["turns"] == 2
    assert summary["metrics"]["M-05"]["latency_ms"] == summary["latency_ms"]
    assert summary["metrics"]["M-05"]["workload"]["parallelism"] == 2
    assert summary["not_stopped"] == 0 and summary["resent"] == 0

    stack = deployed.read(written(tmp_path))
    run_manifest = endtoend.manifest(
        "run",
        (datetime.now(UTC), datetime.now(UTC)),
        {"commit": "0" * 40, "clean": True},
        stack,
        "caller",
        manifest,
        chosen,
        {"situations": [], "languages": ["es"], "limit": None},
        2,
        played,
        keeper.hashes,
    )
    assert run_manifest["grader"] == grader.VERSION
    assert run_manifest["set"]["sha256"] == manifest["sha256"]
    assert run_manifest["stack"]["runs_as"] == "caller"
    assert len(run_manifest["stack"]["versions"]) == 1
    assert {m["model_returned"][0] for m in run_manifest["models"]} == {"scripted"}
    assert run_manifest["totals"]["turns"] == sum(len(e["turns"]) for _, e, _ in played)
    assert sorted(run_manifest["results"]) == [f"cases/{n:04d}.json" for n in (1, 2, 3)]
    assert json.dumps(run_manifest)
