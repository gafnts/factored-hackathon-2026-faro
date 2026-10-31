"""
A handoff is never dropped: a failing summary takes the reason's fixed text, and each failing fact, action, piece of
evidence, statement, or question is left out, each named by its path and rule, never its value; what pruning can't mend
is a fault (ADR-0004, The handoff; CTL-05, OPS-05, POL-11, POL-46).
"""

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from faro.policy.handoffs import HANDOFFS
from faro.policy.payload import PayloadError, checked

POLICY = Path(__file__).resolve().parents[3] / "docs" / "policy" / "card-support.md"
DIGITS = "4123 4567 8901 4821"


def example() -> dict[str, Any]:
    text = POLICY.read_text(encoding="utf-8")
    payload: dict[str, Any] = json.loads(
        re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)[0]
    )
    return payload


def test_a_valid_handoff_passes_unchanged() -> None:
    payload = example()

    assert checked(payload) == (payload, [])


def test_a_summary_that_could_hold_a_card_number_takes_the_fixed_text() -> None:
    payload = example()
    payload["request"]["summary"] = f"Tarjeta {DIGITS}"

    mended, errors = checked(payload)

    assert mended["request"]["summary"] == HANDOFFS["unrecognized_charge"].summary
    assert errors == [{"path": "/request/summary", "rule": "not"}]
    assert payload["request"]["summary"] == f"Tarjeta {DIGITS}"


def test_each_failing_part_is_left_out_on_its_own() -> None:
    payload = example()
    payload["customer_statements"].append(DIGITS)
    payload["unresolved_questions"].append("x" * 281)
    payload["verified_facts"][5]["value"] = DIGITS
    payload["evidence"][0]["outcome"] = "maybe"
    payload["actions"][0]["outcome"] = "done"

    mended, errors = checked(payload)

    assert {e["path"] for e in errors} == {
        "/customer_statements/1",
        "/unresolved_questions/1",
        "/verified_facts/5/value",
        "/evidence/0/outcome",
        "/actions/0/outcome",
    }
    assert len(mended["customer_statements"]) == 1
    assert len(mended["unresolved_questions"]) == 1
    assert len(mended["verified_facts"]) == len(example()["verified_facts"]) - 1
    assert (mended["actions"], len(mended["evidence"])) == ([], 3)
    assert DIGITS not in json.dumps([mended, errors])


def test_too_many_facts_are_cut_to_the_limit() -> None:
    payload = example()
    payload["verified_facts"] = payload["verified_facts"] * 6

    mended, errors = checked(payload)

    assert len(mended["verified_facts"]) == 60
    assert errors == [{"path": "/verified_facts", "rule": "maxItems"}]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("queue", "customer_service"),
        ("customer_id", "CLI-example"),
        ("reason_code", "fraud"),
        ("schema_version", 2),
    ],
)
def test_what_pruning_cant_mend_is_a_fault_named_by_path(
    field: str, value: Any
) -> None:
    payload = copy.deepcopy(example())
    payload[field] = value

    with pytest.raises(PayloadError) as raised:
        checked(payload)

    assert any(e["path"] == f"/{field}" for e in raised.value.errors)
    assert str(value) not in str(raised.value)
