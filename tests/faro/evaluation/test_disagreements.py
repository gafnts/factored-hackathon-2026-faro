"""
The disagreement log: its entries pass their checks, its page is current, an open entry lets through only the findings
it matches, and the checks catch an entry that could carry a record's value.
"""

import copy
from typing import Any

import pytest

from faro.evaluation import disagreements

ENTRY: dict[str, Any] = {
    "id": "D-901",
    "status": "open",
    "verdict": None,
    "rules": ["POL-14"],
    "question": "A question in our words, citing POL-14.",
    "matches": [
        {
            "situation": "status.which_card",
            "turn": 1,
            "check": "fact",
            "expected": "{cards}",
            "observed": "missing",
        }
    ],
}
FINDING = {"turn": 1, "check": "fact", "expected": "{cards}", "observed": "missing"}


def test_the_logs_entries_pass_their_checks() -> None:
    assert disagreements.problems(disagreements.load()) == []


def test_the_page_is_generated_from_the_entries() -> None:
    page = disagreements.PAGE.read_text(encoding="utf-8")

    assert page == disagreements.render(disagreements.load()), "run make disagreements"


@pytest.mark.parametrize("language", ["es", "pt"])
def test_an_open_entry_covers_the_finding_it_matches_in_any_language_it_names(
    language: str,
) -> None:
    case = {"situation": "status.which_card", "language": language}
    only_pt = copy.deepcopy(ENTRY)
    only_pt["matches"][0]["language"] = "pt"

    assert disagreements.covers([ENTRY], case, FINDING) == "D-901"
    assert (disagreements.covers([only_pt], case, FINDING) is not None) == (
        language == "pt"
    )


def test_a_closed_entry_or_another_finding_isnt_covered() -> None:
    case = {"situation": "status.which_card", "language": "es"}
    closed = {**ENTRY, "status": "closed", "verdict": "oracle", "resolution": "Fixed."}

    assert disagreements.covers([closed], case, FINDING) is None
    assert disagreements.covers([ENTRY], case, {**FINDING, "turn": 2}) is None
    assert (
        disagreements.covers([ENTRY], {**case, "situation": "status.one_card"}, FINDING)
        is None
    )


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"question": "The card ending in 4821 was read."}, "isn't a citation"),
        ({"status": "closed"}, "a closed entry has a verdict"),
        ({"rules": ["rule 14"]}, "rules are POL- IDs"),
        ({"matches": [{**ENTRY["matches"][0], "check": "reply"}]}, "unknown check"),
        (
            {"matches": [{**ENTRY["matches"][0], "observed": "Tarjeta de crédito"}]},
            "enums, tools",
        ),
        (
            {"matches": [{**ENTRY["matches"][0], "customer_id": "CLI-1"}]},
            "a match names only",
        ),
    ],
)
def test_the_checks_catch_an_entry_that_could_carry_a_value(
    change: dict[str, Any], problem: str
) -> None:
    found = disagreements.problems([{**ENTRY, **change}])

    assert any(problem in f for f in found), found
