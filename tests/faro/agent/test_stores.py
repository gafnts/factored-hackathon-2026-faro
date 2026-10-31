"""
The Runtime's own tables: a runtime session stays bound to the user who opened it until its binding expires, and the
execution record is only appended to, each entry checked against its contract first and named by kind and path when
it fails, never by value (ADR-0004, Threads and runtime sessions, Stores, and the amendment on the entrypoint's checks;
SEC-05, OPS-02, OPS-10, POL-11).
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from faro.agent.records import DynamoRecords, RecordError, Turn
from faro.agent.sessions import LIFETIME, DynamoBindings

AT = datetime(2026, 9, 29, 18, 0, tzinfo=UTC)
A, B = "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50", "7c1e2a94-3b5d-4f08-a6e2-9d4b0c8f1e37"
SIGN_IN = "5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68"


@pytest.fixture
def dynamodb() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="bindings",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "runtime_session_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "runtime_session_id", "AttributeType": "S"}
            ],
        )
        client.create_table(
            TableName="records",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "sign_in", "KeyType": "HASH"},
                {"AttributeName": "entry_key", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "sign_in", "AttributeType": "S"},
                {"AttributeName": "entry_key", "AttributeType": "S"},
            ],
        )
        yield client


def test_a_session_stays_with_the_user_who_opened_it(dynamodb: Any) -> None:
    bindings = DynamoBindings(dynamodb, "bindings")

    assert bindings.bind("session-1", A, AT)
    assert bindings.bind("session-1", A, AT + timedelta(minutes=5))
    assert not bindings.bind("session-1", B, AT + timedelta(minutes=5))

    item = dynamodb.get_item(
        TableName="bindings", Key={"runtime_session_id": {"S": "session-1"}}
    )["Item"]
    assert item["sub"]["S"] == A
    assert int(item["expires_at"]["N"]) == int((AT + LIFETIME).timestamp())


def test_an_expired_binding_can_be_bound_again(dynamodb: Any) -> None:
    bindings = DynamoBindings(dynamodb, "bindings")
    bindings.bind("session-1", A, AT)

    assert bindings.bind("session-1", B, AT + LIFETIME + timedelta(seconds=1))
    assert not bindings.bind("session-1", A, AT + LIFETIME + timedelta(minutes=1))


def test_a_binding_failure_other_than_the_condition_is_raised(dynamodb: Any) -> None:
    with pytest.raises(ClientError):
        DynamoBindings(dynamodb, "missing").bind("session-1", A, AT)


def turn(table: Any) -> Turn:
    return Turn(DynamoRecords(table), SIGN_IN, "demo", AT, now=lambda: AT)


def refused(**fields: Any) -> dict[str, Any]:
    return {
        "code": "session_refused",
        "sub": A,
        "runtime_session_id": "session-1",
        "client_thread_id": "thread-0001",
        **fields,
    }


def test_entries_are_keyed_by_the_sign_in_and_kept_90_days(dynamodb: Any) -> None:
    table = boto3.resource("dynamodb", region_name="us-east-1").Table("records")

    entry = asyncio.run(turn(table).write("request_refused", **refused()))

    stored = table.get_item(Key={"sign_in": SIGN_IN, "entry_key": entry["entry_key"]})
    assert stored["Item"]["code"] == "session_refused"
    assert entry["entry_key"].startswith("2026-09-29T18:00:00.000Z#")
    assert entry["entry_key"].endswith("#0000")
    assert entry["expires_at"] == int((AT + timedelta(days=90)).timestamp())


def test_an_entry_is_never_rewritten(dynamodb: Any) -> None:
    table = boto3.resource("dynamodb", region_name="us-east-1").Table("records")
    written = turn(table)
    entry = asyncio.run(written.write("request_refused", **refused()))

    with pytest.raises(ClientError, match="ConditionalCheckFailed"):
        DynamoRecords(table).put({**entry, "code": "internal"})


def test_numbers_are_stored_as_numbers(dynamodb: Any) -> None:
    table = boto3.resource("dynamodb", region_name="us-east-1").Table("records")

    DynamoRecords(table).put(
        {"sign_in": SIGN_IN, "entry_key": "k", "cost_usd": 0.00027, "seq": 1}
    )

    item = table.get_item(Key={"sign_in": SIGN_IN, "entry_key": "k"})["Item"]
    assert (item["cost_usd"], item["seq"]) == (Decimal("0.00027"), 1)


class Kept:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def put(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)


def test_an_entry_that_breaks_its_contract_is_named_by_path_never_by_value() -> None:
    kept = Kept()
    written = Turn(kept, SIGN_IN, "demo", AT, now=lambda: AT)
    typed = "4123456789014821"

    with pytest.raises(RecordError) as raised:
        asyncio.run(
            written.write("request_refused", **refused(client_thread_id=typed * 5))
        )

    assert typed not in str(raised.value)
    assert "request_refused" in str(raised.value)
    assert kept.entries == []
    assert written.seq == 0


def test_the_turns_totals_count_its_calls() -> None:
    written = Turn(Kept(), SIGN_IN, "demo", AT, now=lambda: AT)
    usage = {"input_tokens": 100, "output_tokens": 20}

    written.count({"kind": "model_call", "usage": usage, "cost_usd": 0.0002})
    written.count({"kind": "tool_call"})
    written.count({"kind": "model_call", "usage": usage, "cost_usd": 0.0002})

    assert written.totals() == {
        "model_calls": 2,
        "tool_calls": 1,
        "input_tokens": 200,
        "output_tokens": 40,
        "cost_usd": 0.0004,
    }
    written.count(
        {
            "kind": "model_call",
            "usage": {"input_tokens": None, "output_tokens": None},
            "cost_usd": None,
        }
    )
    assert written.totals()["cost_usd"] is None
