"""
The held-out side (ADR-0005, The split), on fixture sets drawn from the bank's held-out customer, never the real set: a
redraw once a run names the set keeps only the committed set, a set plays only as its committed manifest describes it,
in process with the baseline alone and end to end with the system, and the held-out workload keeps the selection's
proportions at the scope rule's sizes.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from faro.evaluation import (
    bronze,
    cases,
    endtoend,
    families,
    generator,
    heldout,
    runs,
)
from faro.evaluation.facts import contract_words

from .bank import Bank

pytestmark = pytest.mark.xdist_group("evaluation_bank")

SITUATIONS = ("status.one_card", "block.reason_given", "person.asked")


@pytest.fixture(scope="module")
def drawn(bank: Bank) -> generator.Drawn:
    loaded, answers = families.load(), families.load_answers()
    held = families.held_out_ids(loaded, answers)
    with bronze.connect(bank.database, "held_out") as con:
        drawing = generator.Generator(
            con, "held_out", loaded, answers, held, contract_words(), reuse=True
        )
        found = drawing.draw(heldout.NAME, 7, generator.held_out(240))
    return generator.Drawn([c for c in found.cases if c["situation"] in SITUATIONS], [])


def kept(tmp_path: Path, drawn: generator.Drawn) -> tuple[Path, dict[str, Any]]:
    set_path = tmp_path / "sets" / f"{heldout.NAME}.jsonl"
    cases.write(set_path, drawn.cases)
    return set_path, generator.manifest(heldout.NAME, 7, drawn, {})


def run_file(path: Path, run: str, set_name: str, reported: bool) -> None:
    manifest = {"run": run, "set": {"name": set_name}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"manifest": manifest} if reported else manifest), encoding="utf-8"
    )


def test_the_runs_against_a_set_are_found_reported_or_kept(tmp_path: Path) -> None:
    reported, local = tmp_path / "docs", tmp_path / "data"
    run_file(
        reported / "20261003T010000Z-aaaa.json",
        "20261003T010000Z-aaaa",
        "selection",
        True,
    )

    assert heldout.runs_against(heldout.NAME, reported, local) == []
    run_file(
        local / "20261003T020000Z-bbbb" / "manifest.json",
        "20261003T020000Z-bbbb",
        heldout.NAME,
        False,
    )
    run_file(
        reported / "20261003T030000Z-cccc.json",
        "20261003T030000Z-cccc",
        heldout.NAME,
        True,
    )

    assert heldout.runs_against(heldout.NAME, reported, local) == [
        "20261003T020000Z-bbbb",
        "20261003T030000Z-cccc",
    ]


def test_a_redraw_after_a_run_keeps_only_the_set_its_committed_manifest_describes(
    drawn: generator.Drawn,
) -> None:
    manifest = generator.manifest(heldout.NAME, 7, drawn, {})
    played = ["20261003T030000Z-cccc"]

    heldout.reproduce(played, drawn.cases, manifest, True)
    for found, is_committed in ((drawn.cases, False), (drawn.cases[1:], True)):
        with pytest.raises(heldout.HeldOutError, match="a redraw only rebuilds it"):
            heldout.reproduce(played, found, manifest, is_committed)


def test_a_held_out_set_plays_only_as_its_committed_manifest_describes_it(
    drawn: generator.Drawn,
) -> None:
    manifest = generator.manifest(heldout.NAME, 7, drawn, {})
    edited = [dict(c) for c in drawn.cases]
    edited[0]["language"] = "pt" if edited[0]["language"] == "es" else "es"

    heldout.verify(drawn.cases, manifest, True)
    with pytest.raises(heldout.HeldOutError, match="isn't committed"):
        heldout.verify(drawn.cases, manifest, False)
    with pytest.raises(heldout.HeldOutError, match="no held-out case is edited"):
        heldout.verify(edited, manifest, True)


def test_git_says_whether_a_manifest_is_committed(tmp_path: Path) -> None:
    loose = tmp_path / "held_out.json"
    loose.write_text("{}", encoding="utf-8")

    assert heldout.committed(Path("docs/adr/0001-deploy-to-us-east-1.md"))
    assert not heldout.committed(loose)


def test_the_held_out_workload_keeps_the_selections_proportions_at_each_size() -> None:
    selection = generator.COMPOSITIONS["selection"]

    for size in generator.HELD_OUT_SIZES:
        workload = generator.held_out(size)
        assert workload.keys() == selection.keys()
        assert abs(2 * sum(workload.values()) - size) <= size * 0.1
        assert min(workload.values()) >= 1
    assert generator.held_out(600)["status.one_card"] > selection["status.one_card"]


def test_the_held_out_side_draws_every_case_on_its_side(drawn: generator.Drawn) -> None:
    assert drawn.cases
    assert {c["side"] for c in drawn.cases} == {"held_out"}
    assert {c["set"] for c in drawn.cases} == {heldout.NAME}


def test_the_held_out_side_plays_in_process_with_the_baseline_alone(
    bank: Bank, drawn: generator.Drawn, tmp_path: Path
) -> None:
    set_path, manifest = kept(tmp_path, drawn)

    summary = runs.play_set(
        set_path,
        bank.database,
        tmp_path / "run",
        {},
        "baseline",
        None,
        (manifest, True),
    )

    assert (summary["cases"], summary["errors"]) == (len(drawn.cases), 0)
    for models, held in (("scripted", (manifest, True)), ("baseline", None)):
        with pytest.raises(runs.PlayError):
            runs.play_set(
                set_path, bank.database, tmp_path / "again", {}, models, None, held
            )
    with pytest.raises(runs.PlayError, match="isn't committed"):
        runs.play_set(
            set_path,
            bank.database,
            tmp_path / "loose",
            {},
            "baseline",
            None,
            (manifest, False),
        )


def test_end_to_end_takes_a_committed_held_out_set_and_no_mixed_one(
    drawn: generator.Drawn, tmp_path: Path
) -> None:
    set_path, manifest = kept(tmp_path, drawn)
    mixed = tmp_path / "mixed.jsonl"
    cases.write(mixed, [*drawn.cases, {**drawn.cases[0], "side": "development"}])
    mixed_manifest = generator.manifest(
        "mixed", 7, generator.Drawn(list(cases.read(mixed)), []), {}
    )

    assert endtoend.drawn(set_path, manifest, committed=True) == drawn.cases
    with pytest.raises(endtoend.RunError, match="isn't committed"):
        endtoend.drawn(set_path, manifest)
    with pytest.raises(endtoend.RunError, match="one side of the split"):
        endtoend.drawn(mixed, mixed_manifest, committed=True)
