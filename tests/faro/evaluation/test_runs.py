"""
A drawn set played in process from a pipeline build, here the bank's: the tools' items are read for the set's
customers only, each case's evidence and grade are kept, the summary holds counts, situations, and checks alone, and
the manifest says which models answered, with no provider, no latency, and the baseline's cost of zero. A model call
that no retry fixes stops a run, which is kept as void.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from langchain_core.runnables import RunnableLambda

from faro.agent.models import MODEL, PROVIDER, Declared
from faro.evaluation import __main__ as cli
from faro.evaluation import bronze, cases, families, generator, runs
from faro.evaluation.facts import contract_words
from faro.evaluation.scripted import ScriptedModels

from .bank import Bank

pytestmark = pytest.mark.xdist_group("evaluation_bank")


@pytest.fixture(scope="module")
def drawn(bank: Bank) -> list[dict[str, Any]]:
    loaded, answers = families.load(), families.load_answers()
    held = families.held_out_ids(loaded, answers)
    with bronze.connect(bank.database, "development") as con:
        drawing = generator.Generator(
            con, "development", loaded, answers, held, contract_words(), reuse=True
        )
        return drawing.draw("regression", 7).cases


def test_the_tools_items_are_the_exports_for_the_customers_named(bank: Bank) -> None:
    wanted = {"CLI-EXAMPLE00001"}

    items = bronze.tools_items(bank.database, wanted)

    assert {i["pk"] for i in items} == {"META", *wanted}
    exported = [i for i in bank.items if i["pk"] in ("META", *wanted)]
    assert sorted(items, key=lambda i: (i["pk"], i["sk"])) == sorted(
        exported, key=lambda i: (i["pk"], i["sk"])
    )


def test_a_set_is_played_kept_and_summarized_without_a_value(
    bank: Bank, drawn: list[dict[str, Any]], tmp_path: Path
) -> None:
    chosen = [
        c for c in drawn if c["situation"] in ("status.one_card", "status.which_card")
    ]
    set_path = tmp_path / "sets" / "regression.jsonl"
    cases.write(set_path, chosen)
    out = tmp_path / "runs" / runs.run_id()

    summary = runs.play_set(set_path, bank.database, out, {"snapshot": "bank"})

    assert (summary["cases"], summary["errors"], summary["unsafe"]) == (
        len(chosen),
        0,
        0,
    )
    # D-004's match is gone since the question lists the cards under {card_list}.
    assert (summary["covered"], summary["uncovered"]) == ({}, {})
    assert summary["passed"] == len(chosen)
    for kept in ("evidence.jsonl", "grades.jsonl"):
        assert len((out / kept).read_text(encoding="utf-8").splitlines()) == len(chosen)
    written = (out / "summary.json").read_text(encoding="utf-8")
    assert json.loads(written)["passed"] == summary["passed"]
    assert not any(c["customer_id"] in written for c in chosen)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert {(m["model"], m["provider"]) for m in manifest["models"]} == {
        ("scripted", None)
    }


def test_the_baseline_plays_in_process_and_its_manifest_names_no_provider(
    bank: Bank, drawn: list[dict[str, Any]], tmp_path: Path
) -> None:
    chosen = [
        c
        for c in drawn
        if c["situation"] in ("status.one_card", "block.reason_given", "person.asked")
    ]
    set_path = tmp_path / "sets" / "regression.jsonl"
    cases.write(set_path, chosen)
    out = tmp_path / "runs" / runs.run_id()
    code = {"commit": "0" * 40, "clean": True}

    summary = runs.play_set(
        set_path, bank.database, out, {"snapshot": "bank"}, "baseline", code
    )

    assert (summary["models"], summary["cases"], summary["errors"]) == (
        "baseline",
        len(chosen),
        0,
    )
    written = (out / "manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(written)
    assert (manifest["mode"], manifest["system"], manifest["code"]) == (
        "in_process",
        "baseline",
        code,
    )
    assert {"route", "reply"} <= {m["node"] for m in manifest["models"]}
    assert {(m["model"], m["provider"]) for m in manifest["models"]} == {
        ("baseline", None)
    }
    assert manifest["totals"]["cost_usd"] == 0.0
    assert manifest["totals"]["model_calls"] > 0
    assert "latency" not in written
    assert manifest["results"] == {
        name: hashlib.sha256((out / name).read_bytes()).hexdigest()
        for name in ("evidence.jsonl", "grades.jsonl", "summary.json")
    }
    assert not any(c["customer_id"] in written for c in chosen)


def test_only_development_cases_play_with_the_scripted_models(
    bank: Bank, drawn: list[dict[str, Any]], tmp_path: Path
) -> None:
    set_path = tmp_path / "held_out.jsonl"
    cases.write(set_path, [{**drawn[0], "side": "held_out"}])

    with pytest.raises(runs.PlayError):
        runs.play_set(set_path, bank.database, tmp_path / "run", {})


def test_the_run_index_lists_each_committed_run_and_says_when_there_are_none(
    tmp_path: Path,
) -> None:
    reported, page = tmp_path / "runs", tmp_path / "runs.md"

    none = runs.index(reported, page)
    (reported).mkdir()
    (reported / "20261002T120000Z-abcd.json").write_text(
        json.dumps(
            {
                "purpose": "pilot",
                "manifest": {
                    "run": "20261002T120000Z-abcd",
                    "mode": "end_to_end",
                    "started_at": "2026-10-02T12:00:00+00:00",
                    "set": {"name": "regression", "cases": 20},
                    "stack": {"environment": "prototype"},
                    "grader": 4,
                    "totals": {"cost_usd": 0.0231},
                },
                "summary": {"cases": 20, "passed": 19},
            }
        ),
        encoding="utf-8",
    )
    one = runs.index(reported, page)

    written = page.read_text(encoding="utf-8")
    assert (none, one) == (0, 1)
    assert "| 20261002T120000Z-abcd | 2026-10-02 | pilot | end_to_end |" in written
    assert "| regression (20) | prototype | 4 | 19 of 20 | 0.02 |" in written
    assert "No reported run" not in written


def test_the_run_index_marks_a_void_run_with_its_cause(tmp_path: Path) -> None:
    reported, page = tmp_path / "runs", tmp_path / "runs.md"
    reported.mkdir()
    (reported / "20261104T120000Z-abcd.json").write_text(
        json.dumps(
            {
                "purpose": "selection",
                "manifest": {
                    "run": "20261104T120000Z-abcd",
                    "mode": "end_to_end",
                    "started_at": "2026-11-04T12:00:00+00:00",
                    "set": {"name": "selection", "cases": 179},
                    "stack": {"environment": "local"},
                    "grader": 8,
                    "totals": {"cost_usd": 0.31},
                },
                "summary": {
                    "cases": 40,
                    "passed": 31,
                    "void": {"outcome": "spend_limit", "case_id": "selection-0040"},
                },
            }
        ),
        encoding="utf-8",
    )

    runs.index(reported, page)

    assert "| 31 of 40, void (spend_limit) | 0.31 |" in page.read_text(encoding="utf-8")


def model_call(outcome: str) -> dict[str, Any]:
    return {"kind": "model_call", "node": "route", "outcome": outcome}


@pytest.mark.parametrize(
    ("outcomes", "stops"),
    [
        (["ok", "rate_limited", "ok"], None),
        (["invalid_output", "server_error", "refused"], None),
        (["rate_limited", "spend_limit"], "spend_limit"),
        (["key_rejected"], "key_rejected"),
        (["model_not_found"], "model_not_found"),
        (["invalid_request", "spend_limit"], "invalid_request"),
        # 1.10.0's Runtime records a spent limit, as every 4xx but a 429, as error.
        (["error"], "error"),
    ],
)
def test_a_model_call_no_retry_fixes_stops_a_run(
    outcomes: list[str], stops: str | None
) -> None:
    record = [
        {"kind": "tool_call", "tool": "get_card", "outcome": "failed"},
        *(model_call(o) for o in outcomes),
    ]

    assert runs.stopped(record) == stops


SPENT = anthropic.BadRequestError(
    "spent",
    response=httpx2.Response(
        400, request=httpx2.Request("POST", "https://api.anthropic.example")
    ),
    body={
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "You have reached your specified workspace API usage limits.",
        },
    },
)


def test_a_spent_limit_stops_a_run_in_process_and_the_run_is_kept_void(
    bank: Bank,
    drawn: list[dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chosen = [
        c for c in drawn if c["situation"] in ("status.one_card", "status.which_card")
    ]
    assert len(chosen) > 2
    spent_at = chosen[1]["case_id"]
    started: list[str] = []

    def spent(_: Any) -> Any:
        raise SPENT

    def scripted(case: dict[str, Any], *rest: Any) -> Any:
        started.append(case["case_id"])
        if case["case_id"] == spent_at:
            return Declared(lambda _: RunnableLambda(spent), PROVIDER, MODEL)
        return ScriptedModels(case, *rest)

    monkeypatch.setattr(runs, "ScriptedModels", scripted)
    set_path = tmp_path / "sets" / "regression.jsonl"
    cases.write(set_path, chosen)
    out = tmp_path / "runs" / runs.run_id()

    summary = runs.play_set(set_path, bank.database, out, {"snapshot": "bank"})

    void = {"outcome": "spend_limit", "case_id": spent_at}
    assert started == [c["case_id"] for c in chosen[:2]]
    assert (summary["void"], summary["cases"], summary["passed"]) == (void, 2, 1)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["void"] == void


def test_a_void_run_says_why_and_what_to_do_and_exits_in_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert set(cli.REMEDIES) == set(runs.STOPPING)
    summary = {"cases": 2, "void": {"outcome": "spend_limit", "case_id": "case-2"}}

    assert cli.voided(summary)
    said = capsys.readouterr().err
    assert "a model call in case-2 ended spend_limit" in said
    assert "raise the provider's spend limit" in said
    assert not cli.voided({"cases": 2, "void": None})

    monkeypatch.setattr(cli, "play", lambda *_: True)
    assert cli.main(["play"]) == 1
