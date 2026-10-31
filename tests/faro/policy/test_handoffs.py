"""
The handoff reasons in code are the policy's table, row for row, and each fixed summary is free text the handoff schema
accepts (POL-45 to POL-47; OPS-05).
"""

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from faro.policy import handoff_schema
from faro.policy.handoffs import HANDOFFS, urgent

POLICY = Path(__file__).resolve().parents[3] / "docs" / "policy" / "card-support.md"


def table() -> list[tuple[str, str, tuple[str, ...], str]]:
    text = POLICY.read_text(encoding="utf-8")
    section = text.split("\n### Handoff reasons\n", 1)[1].split("\n#", 1)[0]
    rows = re.findall(
        r"^\| `([a-z_]+)` \| (Required|Offered) \| ([^|]+) \| `([a-z_]+)` \|$",
        section,
        re.MULTILINE,
    )
    return [
        (code, kind.lower(), tuple(r.strip() for r in rules.split(",")), queue)
        for code, kind, rules, queue in rows
    ]


def test_the_reasons_are_the_policys_table_row_for_row() -> None:
    rows = table()

    assert len(rows) == len(handoff_schema()["properties"]["reason_code"]["enum"])
    assert [(code, r.handoff, r.rules, r.queue) for code, r in HANDOFFS.items()] == rows


def test_the_reasons_are_the_handoff_schemas_in_its_order() -> None:
    assert list(HANDOFFS) == handoff_schema()["properties"]["reason_code"]["enum"]


@pytest.mark.parametrize("code", list(HANDOFFS))
def test_each_fixed_summary_is_text_the_schema_accepts(code: str) -> None:
    summary = handoff_schema()["properties"]["request"]["properties"]["summary"]
    check = Draft202012Validator(
        {**summary, "$defs": handoff_schema()["$defs"]},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )

    check.validate(HANDOFFS[code].summary)


def example() -> dict[str, Any]:
    text = POLICY.read_text(encoding="utf-8")
    found: dict[str, Any] = json.loads(
        re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)[0]
    )
    return found


def ended(payload: dict[str, Any], outcome: str, reason: str) -> dict[str, Any]:
    changed = copy.deepcopy(payload)
    changed["actions"][0] |= {"outcome": outcome, "reason": reason}
    changed["verified_facts"] = [
        f for f in changed["verified_facts"] if f["field"] != "product_status"
    ]
    return changed


def card_status(payload: dict[str, Any], status: str) -> dict[str, Any]:
    changed = copy.deepcopy(payload)
    changed["actions"] = []
    for fact in changed["verified_facts"]:
        if fact["field"] == "product_status":
            fact["value"] = status
    return changed


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("verified block of the charged card", False),
        ("charge, block cancelled", True),
        ("charge, block lapsed", True),
        ("charge, block not verified", True),
        ("charge, card already blocked", False),
        ("charge, card closed", True),
        ("lost card, block lapsed", True),
        ("stolen card, block verified", False),
        ("request for a person", False),
    ],
)
def test_a_handoff_is_urgent_when_a_reported_card_isnt_verified_blocked(
    case: str, expected: bool
) -> None:
    # POL-47.
    payload = example()
    person = {**card_status(payload, "Active"), "reason_code": "customer_request"}
    person["request"] = {**person["request"], "label": "talk_to_human"}
    person["queue"] = "customer_service"
    cases = {
        "verified block of the charged card": payload,
        "charge, block cancelled": ended(
            payload, "declined_by_customer", "unrecognized_charge"
        ),
        "charge, block lapsed": ended(payload, "lapsed", "unrecognized_charge"),
        "charge, block not verified": ended(
            payload, "not_verified", "unrecognized_charge"
        ),
        "charge, card already blocked": card_status(payload, "Blocked"),
        "charge, card closed": card_status(payload, "Closed"),
        "lost card, block lapsed": {
            **ended(payload, "lapsed", "lost"),
            "reason_code": "block_lapsed",
        },
        "stolen card, block verified": {
            **ended(payload, "verified", "stolen"),
            "reason_code": "unsupported_request",
        },
        "request for a person": person,
    }

    assert urgent(cases[case]) is expected
