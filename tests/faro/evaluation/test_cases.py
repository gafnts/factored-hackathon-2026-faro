"""
The case format: a case validates against case.schema.json and the execution record's enums, and the checks the schema
can't state catch a turn that contradicts itself.
"""

import copy
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from faro.evaluation import cases

# On the contracts' example customer, so the case holds no snapshot value.
EXAMPLE: dict[str, Any] = {
    "version": 1,
    "case_id": cases.case_id("example", 7, 1),
    "set": "example",
    "side": "development",
    "group": "reads",
    "situation": "status.several_cards",
    "source": "natural",
    "language": "es",
    "customer_id": "CLI-EXAMPLE00001",
    "family_id": "card_status-01",
    "script": {
        "messages": [{"id": "card_status-01/es/0", "text": "¿Cómo está mi tarjeta?"}],
        "answers": {
            "card": {"id": "card_last_four-01/es", "text": "La que termina en 4821."},
            "confirm_control": "confirm",
        },
        "means": {"product_id": "PRD-EXAMPLE00002"},
    },
    "fixtures": [],
    "faults": [],
    "expected": {
        "turns": [
            {
                "sends": "message",
                "decisions": [
                    {
                        "request_label": "card_status",
                        "outcome_class": "clarify",
                        "rules": ["POL-14"],
                    }
                ],
                "language": "es",
                "awaiting": "card",
                "tools_required": ["list_cards"],
                "tools_forbidden": ["block_card"],
                "facts": {},
                "withheld": [],
            },
            {
                "sends": "card",
                "decisions": [
                    {
                        "request_label": "card_status",
                        "outcome_class": "answer",
                        "rules": ["POL-20"],
                    }
                ],
                "language": "es",
                "awaiting": "none",
                "tools_required": ["get_card"],
                "tools_forbidden": ["block_card"],
                "facts": {
                    "{card}": "tarjeta de crédito terminada en 4821",
                    "{card.status}": "activa",
                },
                "withheld": [],
            },
        ],
        "blocked": [],
        "rules": ["POL-14", "POL-20"],
        "policy_version": 2,
    },
}


def example() -> dict[str, Any]:
    return copy.deepcopy(EXAMPLE)


def test_the_example_case_is_well_formed() -> None:
    assert cases.problems(example()) == []


def test_a_case_expects_only_what_the_record_can_hold() -> None:
    case = example()
    case["expected"]["turns"][0]["decisions"][0]["request_label"] = "card_balance"
    case["expected"]["turns"][0]["awaiting"] = "account"
    case["faults"] = [{"tool": "get_balance", "failures": 1, "error": "timeout"}]

    found = cases.problems(case)

    assert len(found) == 3
    assert any(
        p.startswith("expected/turns/0/decisions/0/request_label") for p in found
    )
    assert any(p.startswith("expected/turns/0/awaiting") for p in found)
    assert any(p.startswith("faults/0/tool") for p in found)


def test_a_fact_is_named_by_its_placeholder() -> None:
    case = example()
    case["expected"]["turns"][1]["facts"] = {"status": "activa"}

    assert cases.problems(case)


def test_a_turn_that_contradicts_itself_is_caught() -> None:
    case = example()
    turn = case["expected"]["turns"][0]
    turn["tools_forbidden"] = ["list_cards"]
    turn["decisions"].append(
        {"request_label": "block_card", "outcome_class": "block", "rules": []}
    )

    assert cases.problems(case) == [
        "turn 1 both requires and forbids ['list_cards']",
        "turn 1 clarifies before its last request",
    ]


def test_fixtures_and_faults_are_the_overlays_items_without_sign_in_or_ttl() -> None:
    examples = files("faro.contracts").joinpath("examples")
    fixtures = [
        {k: v for k, v in item.items() if k not in ("sign_in", "ttl")}
        for name in ("overlay.fixture_card.json", "overlay.fixture_transaction.json")
        for item in json.loads(examples.joinpath(name).read_text(encoding="utf-8"))
    ]
    built = {
        **copy.deepcopy(EXAMPLE),
        "source": "built",
        "fixtures": fixtures,
        "faults": [{"tool": "get_card", "failures": 3, "error": "timeout"}],
    }

    assert cases.problems(built) == []

    broken = copy.deepcopy(built)
    broken["fixtures"][-1]["listed_at"] = "2026-06-01 00:00:00#TRX-FIXTURE0000000000009"
    broken["fixtures"][0]["customer_id"] = "CLI-EXAMPLE00002"
    del broken["fixtures"][1]["is_fraud"]
    broken["faults"][0]["tool"] = "file_handoff"
    found = cases.problems(broken)
    assert [p.split(":")[0] for p in found] == [
        "fixtures/0",
        "fixtures/1",
        f"fixtures/{len(fixtures) - 1}",
        "faults/0",
    ]
    assert not any("CLI-" in p or "TRX-" in p for p in found)


def test_case_ids_are_opaque_and_stable() -> None:
    first = cases.case_id("regression", 7, 1)

    assert first == cases.case_id("regression", 7, 1)
    assert first != cases.case_id("regression", 7, 2)
    assert first != cases.case_id("selection", 7, 1)
    assert len(first) == 16


def test_a_set_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "sets" / "example.jsonl"

    cases.write(path, [example(), example()])

    assert list(cases.read(path)) == [EXAMPLE, EXAMPLE]
