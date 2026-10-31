"""
The split's guards: a case sits wholly on its side, no development artifact holds a held-out family's words, and only
the bronze opener names the pipeline's database.
"""

from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

import faro.evaluation
from faro.evaluation import baseline, families, guards

from .bank import HELD_OUT_CUSTOMER
from .test_cases import example

HELD = families.held_out_ids(families.load(), families.load_answers())


@pytest.fixture(scope="module")
def held_messages() -> list[tuple[str, str]]:
    return families.texts(families.load(), families.load_answers(), HELD)


def test_a_development_case_on_development_ground_passes() -> None:
    assert guards.case_problems(example(), HELD) == []


def test_a_case_off_its_side_is_caught() -> None:
    case: dict[str, Any] = example()
    case["family_id"] = "card_status-03"
    case["script"]["answers"]["card"]["id"] = "card_last_four-02/es"
    case["script"]["means"]["other_customer_id"] = HELD_OUT_CUSTOMER

    assert guards.case_problems(case, HELD) == [
        "its other customer isn't on the development side",
        "card_last_four-02 isn't on the development side",
        "card_status-03 isn't on the development side",
    ]


def test_a_held_out_case_must_be_held_out_throughout() -> None:
    case: dict[str, Any] = example()
    case["side"] = "held_out"

    found = guards.case_problems(case, HELD)

    assert "its customer isn't on the held_out side" in found
    assert "card_status-01 isn't on the held_out side" in found


def test_a_held_out_case_may_borrow_development_messages_but_not_answers() -> None:
    case: dict[str, Any] = example()
    case["side"], case["phrasing"] = "held_out", "development"

    found = guards.case_problems(case, HELD)

    assert "its customer isn't on the held_out side" in found
    assert "card_status-01 isn't on the held_out side" not in found
    assert "card_last_four-01 isn't on the held_out side" in found


def test_a_development_case_borrows_nothing() -> None:
    case: dict[str, Any] = example()
    case["phrasing"] = "held_out"

    found = guards.case_problems(case, HELD)

    assert found[0] == "a development case borrows no phrasing"
    assert "card_status-01 isn't on the held_out side" in found


def test_a_held_out_message_in_an_artifact_is_caught() -> None:
    held = [
        ("card_status-03/es/0", "Quiero saber si mi tarjeta de crédito sigue activa.")
    ]
    artifacts = {
        "keywords.txt": "estado\nquiero saber si mi tarjeta de credito sigue activa\n",
        "notes.md": "Ejemplo: Quiero saber si mi tarjeta de crédito sigue activa, gracias.",
        "clean.md": "Route each message to the policy's labels.",
    }

    assert guards.leaks(artifacts, held) == [
        "keywords.txt holds held-out card_status-03/es/0",
        "notes.md holds held-out card_status-03/es/0",
        "keywords.txt:2 is too close to held-out card_status-03/es/0",
        "notes.md:1 is too close to held-out card_status-03/es/0",
    ]


def test_the_agents_prompts_hold_no_held_out_message(
    held_messages: list[tuple[str, str]],
) -> None:
    prompts = files("faro.agent").joinpath("prompts")
    artifacts = {
        p.name: p.read_text(encoding="utf-8")
        for p in prompts.iterdir()
        if p.name.endswith(".md")
    }

    assert artifacts
    assert guards.leaks(artifacts, held_messages) == []


def test_the_baselines_words_hold_no_held_out_message(
    held_messages: list[tuple[str, str]],
) -> None:
    # Its keywords, patterns, and templates are authored from development families only (ADR-0005, Baselines).
    source = Path(baseline.__file__)

    assert guards.leaks({source.name: source.read_text("utf-8")}, held_messages) == []


def test_the_openings_suggestions_hold_no_held_out_message(
    held_messages: list[tuple[str, str]],
) -> None:
    # The opening's suggested prompts are the one development artifact whose words a judge sends as messages
    # (ADR-0007's amendment of 2026-10-02); they live in the page's texts.
    texts = Path(__file__).parents[3] / "web" / "src" / "texts.ts"

    assert texts.is_file()
    assert guards.leaks({texts.name: texts.read_text("utf-8")}, held_messages) == []


def test_only_the_bronze_opener_opens_a_database() -> None:
    package = Path(faro.evaluation.__file__).parent
    openers = [
        p.relative_to(package).as_posix()
        for p in package.rglob("*.py")
        if "duckdb.connect" in p.read_text(encoding="utf-8")
        or "attach" in p.read_text(encoding="utf-8")
    ]

    assert openers == ["bronze.py"]
