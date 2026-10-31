"""
The handoff schema (CTL-05) holds what the policy says a handoff may carry (POL-11, POL-32, POL-36, POL-37,
POL-38, POL-46, POL-47), and stays in step with the policy's labels and reason codes.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from faro.policy import handoff_schema

POLICY = Path(__file__).resolve().parents[3] / "docs" / "policy" / "card-support.md"


def policy_text() -> str:
    return POLICY.read_text(encoding="utf-8")


def validator() -> Draft202012Validator:
    return Draft202012Validator(
        handoff_schema(), format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def example() -> dict[str, Any]:
    blocks = re.findall(r"```json\n(.*?)\n```", policy_text(), re.DOTALL)
    assert len(blocks) == 1
    payload: dict[str, Any] = json.loads(blocks[0])
    return payload


def fact(payload: dict[str, Any], field: str) -> dict[str, Any]:
    matches = [f for f in payload["verified_facts"] if f["field"] == field]
    assert len(matches) == 1
    found: dict[str, Any] = matches[0]
    return found


def table_codes(heading: str) -> set[str]:
    section = policy_text().split(f"\n{heading}\n", 1)[1].split("\n#", 1)[0]
    return set(re.findall(r"^\| `([a-z_]+)` \|", section, re.MULTILINE))


def test_the_schema_is_valid_json_schema() -> None:
    Draft202012Validator.check_schema(handoff_schema())


def test_the_policy_example_is_valid() -> None:
    validator().validate(example())


def test_labels_are_the_policy_requests() -> None:
    labels = handoff_schema()["properties"]["request"]["properties"]["label"]["enum"]

    assert set(labels) == table_codes("## Requests")


def test_reason_codes_are_the_policy_handoff_reasons() -> None:
    codes = handoff_schema()["properties"]["reason_code"]["enum"]

    assert set(codes) == table_codes("### Handoff reasons")


def test_every_rule_the_policy_cites_is_defined_once() -> None:
    text = policy_text()
    defined = re.findall(r"^- \*\*(POL-\d{2})\*\*", text, re.MULTILINE)

    assert len(defined) == len(set(defined))
    assert set(re.findall(r"POL-\d{2}", text)) <= set(defined)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("transcript",), "Cliente: hola"),
        (("request", "summary"), "Tarjeta 4123456789012345 bloqueada."),
        (("request", "summary"), "Tarjeta 4123 4567 8901 2345 bloqueada."),
        (("customer_statements", 0), "Mi tarjeta es la 4123-4567-8901-2345."),
        (("queue",), "customer_service"),
        (("reason_code",), "complaint"),
        (("actions", 0, "confirmed_at"), None),
        (("actions", 0, "evidence"), []),
        (("actions", 0, "confirmation_id"), "confirmation-1"),
        (("actions", 0, "outcome"), "confirmed_by_text"),
        (("language",), "en"),
        (("rules", 0), "POL-1"),
        (("rules",), []),
        (("customer_id",), "12345678"),
        (("created_at",), "yesterday"),
    ],
    ids=[
        "a transcript",
        "a card number",
        "a spaced card number",
        "a dashed card number in a statement",
        "an unrecognized charge outside dispute intake",
        "another reason in dispute intake",
        "a verified block without a confirmation",
        "a verified block without evidence",
        "a confirmation ID that isn't one",
        "a block outcome the policy doesn't define",
        "an unsupported language",
        "a malformed rule ID",
        "no rule",
        "a customer ID in another shape",
        "a date-time that isn't one",
    ],
)
def test_the_schema_rejects(path: tuple[str | int, ...], value: object) -> None:
    payload = example()
    target: Any = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    assert not validator().is_valid(payload)


def test_a_card_number_never_passes_as_last_four() -> None:
    payload = example()
    fact(payload, "last_four")["value"] = "4123456789014821"

    assert not validator().is_valid(payload)


def test_the_card_number_is_no_fact() -> None:
    payload = example()
    fact(payload, "last_four")["field"] = "product_number"

    assert not validator().is_valid(payload)


def test_a_fact_names_a_record_of_its_subject() -> None:
    payload = example()
    fact(payload, "merchant_name")["id"] = fact(payload, "product_id")["value"]

    assert not validator().is_valid(payload)


def test_a_field_the_record_doesnt_hold_is_a_null_fact() -> None:
    payload = example()
    fact(payload, "merchant_name")["value"] = None

    assert validator().is_valid(payload)


@pytest.mark.parametrize("outcome", ["declined_by_customer", "lapsed"])
def test_a_block_never_confirmed_has_no_confirmation_time(outcome: str) -> None:
    payload = example()
    action = payload["actions"][0]
    action.update(outcome=outcome, confirmed_at=None, evidence=[])
    assert validator().is_valid(payload)

    action["confirmed_at"] = "2026-10-02T15:41:37Z"
    assert not validator().is_valid(payload)


def test_a_lost_card_left_unblocked_goes_to_customer_service() -> None:
    payload = example()
    payload.update(
        reason_code="block_lapsed",
        queue="customer_service",
        priority="urgent",
        rules=["POL-38"],
    )
    payload["request"]["label"] = "block_card"
    payload["actions"][0].update(
        reason="lost", outcome="lapsed", confirmed_at=None, evidence=[]
    )
    assert validator().is_valid(payload)

    payload["queue"] = "dispute_intake"
    assert not validator().is_valid(payload)


def test_every_offered_block_names_its_confirmation() -> None:
    payload = example()
    action = payload["actions"][0]
    action.update(outcome="lapsed", confirmed_at=None, evidence=[])
    del action["confirmation_id"]

    assert not validator().is_valid(payload)
