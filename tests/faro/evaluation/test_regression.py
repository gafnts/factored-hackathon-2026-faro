"""
CI's regression gate (ADR-0005's amendment of 2026-10-01): the regression set's composition, drawn on the bank, played
in process with the scripted models, and graded. It fails on a case the player couldn't play, on any safety check, and
on a divergence or a failed check that no open entry of the disagreement log matches, so a question of the policy's
wording is triaged in writing instead of blocking a change. make regression and the regression job run it; the default
run leaves it out.
"""

import asyncio
import warnings
from typing import Any

import pytest

from faro.evaluation import (
    bronze,
    disagreements,
    families,
    generator,
    grader,
    player,
)
from faro.evaluation.facts import contract_words
from faro.evaluation.scripted import ScriptedModels

from .bank import Bank

pytestmark = [pytest.mark.regression, pytest.mark.xdist_group("evaluation_bank")]

Graded = list[tuple[dict[str, Any], dict[str, Any]]]


@pytest.fixture(scope="module")
def graded(bank: Bank) -> Graded:
    loaded, answers = families.load(), families.load_answers()
    held = families.held_out_ids(loaded, answers)
    by_family = {f.family_id: f for f in loaded}
    by_answer = {a.answer_id: a for a in answers}
    with bronze.connect(bank.database, "development") as con:
        drawing = generator.Generator(
            con, "development", loaded, answers, held, contract_words(), reuse=True
        )
        drawn = drawing.draw("regression", 7).cases

    async def play_all() -> Graded:
        found = []
        for case in drawn:
            items = [i for i in bank.items if i["pk"] in ("META", case["customer_id"])]
            models = ScriptedModels(case, by_family, by_answer, items)
            evidence = await player.play(case, items, models)
            found.append((case, grader.grade(case, evidence)))
        return found

    return asyncio.run(play_all())


def where(case: dict[str, Any], finding: dict[str, Any] | None = None) -> str:
    named = f"{case['situation']} ({case['language']})"
    if finding is None:
        return named
    return f"{named} turn {finding['turn']}: {finding['check']}"


def test_the_whole_composition_plays(graded: Graded) -> None:
    unplayed = [where(c) + f": {g['error']}" for c, g in graded if g["error"]]

    assert len(graded) == sum(
        n * len(generator.BY_NAME[name].languages)
        for name, n in generator.COMPOSITIONS["regression"].items()
    )
    assert unplayed == []


def test_no_case_is_unsafe(graded: Graded) -> None:
    unsafe = [where(c, f) for c, g in graded for f in g["safety"]]

    assert unsafe == []


def test_every_divergence_and_failed_check_has_an_open_entry(graded: Graded) -> None:
    entries = disagreements.load()
    uncovered = [
        f"{where(c, f)}, expected {f['expected']}, observed {f['observed']}"
        for c, g in graded
        for f in g["divergence"] + g["failures"]
        if disagreements.covers(entries, c, f) is None
    ]

    assert uncovered == [], "triage each in docs/evaluation/disagreements.json"


def test_an_open_entry_that_matches_nothing_is_flagged_for_closing(
    graded: Graded,
) -> None:
    """
    A warning, not a failure: a fix in the system shouldn't wait on the log, which is closed with the fix's verdict.
    """
    entries = disagreements.load()
    used = {
        disagreements.covers(entries, c, f)
        for c, g in graded
        for f in g["divergence"] + g["failures"]
    }
    for entry in entries:
        if entry["status"] == "open" and entry["id"] not in used:
            warnings.warn(f"{entry['id']} matches no finding; close it", stacklevel=1)
