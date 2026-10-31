"""
A handoff's evidence, facts, and actions must be what the execution record holds, in the turns the case names; what
isn't is left out and named by its path and rule, and a priority the payload shows should be urgent is raised
(ADR-0004, The handoff, and its amendment of 2026-09-30; CTL-05, OPS-02, POL-47).
"""

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_dynamodb import DynamoDBClient

from faro.agent.records import DynamoRecords as Writer
from faro.contracts import validator
from faro.policy.handoffs import urgent
from faro.tools.provenance import DynamoRecords, MemoryRecords, checked_against

from .conftest import SIGN_IN, file_handoff_example, recorded_example

OWN_CALL = "71f0d3a6-5c2e-4b89-b7d4-3e9a1c6f8b52"
RECORDS = "banking-agent-local-execution-records"


def payload() -> dict[str, Any]:
    found: dict[str, Any] = copy.deepcopy(file_handoff_example()["payload"])
    return found


def check(given: dict[str, Any], entries: list[dict[str, Any]] | None = None) -> Any:
    return checked_against(
        given, recorded_example() if entries is None else entries, OWN_CALL, []
    )


def test_the_recorded_example_fits_the_execution_records_contract() -> None:
    for entry in recorded_example():
        validator("execution-record").validate(entry)


def test_a_payload_the_record_bears_out_passes_whole() -> None:
    assert check(payload()) == (payload(), [])


def test_a_fact_the_recorded_result_doesnt_hold_is_left_out() -> None:
    given = payload()
    amount = next(f for f in given["verified_facts"] if f["field"] == "amount")
    amount["value"] = 18.99
    index = given["verified_facts"].index(amount)

    checked, errors = check(given)

    assert amount not in checked["verified_facts"]
    assert errors == [{"path": f"/verified_facts/{index}", "rule": "unrecorded"}]


def test_a_fact_about_another_record_is_left_out() -> None:
    given = payload()
    card = next(f for f in given["verified_facts"] if f["field"] == "last_four")
    card["id"] = "PRD-EXAMPLE00005"

    checked, errors = check(given)

    assert card not in checked["verified_facts"]
    assert [e["rule"] for e in errors] == ["unrecorded"]


def test_evidence_from_a_turn_the_case_doesnt_name_takes_its_facts_with_it() -> None:
    entries = [e for e in recorded_example() if e["tool"] != "find_transactions"]

    checked, errors = check(payload(), entries)

    assert all(f["subject"] != "transaction" for f in checked["verified_facts"])
    assert {"path": "/evidence/0", "rule": "unrecorded"} in errors
    assert len(errors) == 1 + sum(
        1 for f in payload()["verified_facts"] if f["subject"] == "transaction"
    )


def test_evidence_that_misstates_its_call_is_a_mismatch() -> None:
    given = payload()
    given["evidence"][2]["outcome"] = "error"

    checked, errors = check(given)

    assert {"path": "/evidence/2", "rule": "mismatch"} in errors
    assert all(
        f["evidence"] != given["evidence"][2]["call_id"]
        for f in checked["verified_facts"]
    )


def test_a_fact_citing_this_calls_id_is_exempt_only_if_this_call_added_it() -> None:
    given = payload()
    added = {
        "subject": "customer",
        "id": given["customer_id"],
        "field": "customer_status",
        "value": "Active",
        "evidence": OWN_CALL,
    }
    forged = {
        **added,
        "subject": "card",
        "id": "PRD-EXAMPLE00002",
        "field": "last_four",
        "value": "0000",
    }
    given["verified_facts"] += [added, forged]

    checked, errors = checked_against(given, recorded_example(), OWN_CALL, [added])

    assert added in checked["verified_facts"]
    assert forged not in checked["verified_facts"]
    assert len(errors) == 1


def test_a_verified_block_the_record_doesnt_show_is_left_out_and_raises_urgency() -> (
    None
):
    given = payload()
    entries = [e for e in recorded_example() if e["tool"] != "block_card"]

    checked, _ = check(given, entries)

    assert checked["actions"] == []
    assert urgent(given) is False
    assert urgent({**checked, "verified_facts": []}) is True


@pytest.mark.parametrize(
    ("outcome", "to"), [("declined_by_customer", "cancelled"), ("lapsed", "lapsed")]
)
def test_a_block_that_ended_unused_is_shown_by_its_confirmations_end(
    outcome: str, to: str
) -> None:
    given = payload()
    action = given["actions"][0]
    action |= {"outcome": outcome, "confirmed_at": None, "evidence": []}
    turn = file_handoff_example()["turns"][1]
    ended = {
        "sign_in": SIGN_IN,
        "entry_key": f"{turn}#0009",
        "kind": "confirmation",
        "confirmation_id": action["confirmation_id"],
        "to": to,
    }

    kept, _ = check(given, [*recorded_example(), ended])
    lost, errors = check(given)

    assert kept["actions"] == [action]
    assert lost["actions"] == []
    assert errors == [{"path": "/actions/0", "rule": "unrecorded"}]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[DynamoDBClient]:
    credentials = tmp_path / "aws-credentials"
    credentials.write_text(
        "[default]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName=RECORDS,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": n, "AttributeType": "S"}
                for n in ("sign_in", "entry_key")
            ],
            KeySchema=[
                {"AttributeName": "sign_in", "KeyType": "HASH"},
                {"AttributeName": "entry_key", "KeyType": "RANGE"},
            ],
        )
        writer = Writer(
            boto3.resource("dynamodb", region_name="us-east-1").Table(RECORDS)
        )
        other = {
            **recorded_example()[0],
            "sign_in": "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f",
        }
        for entry in [*recorded_example(), other]:
            writer.put(entry)
        yield dynamodb


def test_the_records_are_read_by_turn_in_the_sign_ins_partition_only(
    client: DynamoDBClient,
) -> None:
    turns = file_handoff_example()["turns"]
    dynamo, memory = DynamoRecords(client, RECORDS), MemoryRecords(recorded_example())

    for prefixes in (turns, turns[:1], turns[1:]):
        read = dynamo.turns(SIGN_IN, prefixes)
        assert sorted(read, key=str) == sorted(memory.turns(SIGN_IN, prefixes), key=str)
    assert len(dynamo.turns(SIGN_IN, turns)) == 3


def test_the_records_can_be_read_by_the_attributes_named_only(
    client: DynamoDBClient,
) -> None:
    # The console's role may name a tool call's attributes only (ADR-0007's amendment of 2026-09-30).
    turns = file_handoff_example()["turns"]
    named = ("sign_in", "entry_key", "kind", "call_id", "result")
    dynamo, memory = DynamoRecords(client, RECORDS), MemoryRecords(recorded_example())

    read = dynamo.turns(SIGN_IN, turns, named)

    assert sorted(read, key=str) == sorted(memory.turns(SIGN_IN, turns, named), key=str)
    assert len(read) == 3
    assert all(set(entry) <= set(named) for entry in read)
    assert any("result" in entry for entry in read)
