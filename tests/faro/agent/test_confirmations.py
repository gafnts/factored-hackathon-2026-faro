"""
A confirmation is created once, taken only from the control, in the thread, user, and sign-in that saw it and before
its time limit, and every change is conditional, in DynamoDB as in memory (ADR-0004, The confirmation; POL-09,
POL-36; CTL-02, EVL-03).
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from faro.agent.confirmations import (
    LIMIT,
    Confirmations,
    DynamoConfirmations,
    MemoryConfirmations,
    new_record,
)

TABLE = "banking-agent-local-confirmations"
AT = datetime(2026, 10, 2, 15, 41, 4, tzinfo=UTC)
ID = "7c1e2a94-3b5d-4f08-a6e2-9d4b0c8f1e37"
BOUND = {
    "thread_key": "11111111-2222-5333-8444-555555555555",
    "sub": "a41c9e27-6b3d-4f58-9e12-7c0d8b5a3f64",
    "origin_jti": "5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68",
}


@pytest.fixture
def dynamo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[DynamoConfirmations]:
    credentials = tmp_path / "aws-credentials"
    credentials.write_text(
        "[default]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "confirmation_id", "AttributeType": "S"}
            ],
            KeySchema=[{"AttributeName": "confirmation_id", "KeyType": "HASH"}],
        )
        yield DynamoConfirmations(client, TABLE)


@pytest.fixture(params=["memory", "dynamo"])
def store(request: pytest.FixtureRequest) -> Confirmations:
    if request.param == "memory":
        return MemoryConfirmations()
    dynamo: DynamoConfirmations = request.getfixturevalue("dynamo")
    return dynamo


def created(store: Confirmations) -> dict[str, Any]:
    record = new_record(ID, BOUND, "CLI-EXAMPLE00001", "PRD-EXAMPLE00002", "lost", AT)
    store.create(record)
    return record


def held(store: Confirmations) -> dict[str, Any]:
    if isinstance(store, MemoryConfirmations):
        return store.records[ID]
    assert isinstance(store, DynamoConfirmations)
    item = store.client.get_item(
        TableName=TABLE, Key={"confirmation_id": {"S": ID}}, ConsistentRead=True
    )["Item"]
    return {k: store.deserializer.deserialize(v) for k, v in item.items()}


def test_a_confirmation_is_pending_for_five_minutes_and_kept_a_day() -> None:
    record = new_record(ID, BOUND, "CLI-EXAMPLE00001", "PRD-EXAMPLE00002", "lost", AT)

    assert record["status"] == "pending"
    assert record["expires_at"] == int((AT + LIMIT).timestamp())
    assert record["ttl"] == int((AT + timedelta(hours=24)).timestamp())
    assert {k: record[k] for k in BOUND} == BOUND


def test_a_confirmation_is_created_once(store: Confirmations) -> None:
    record = created(store)

    with pytest.raises((ClientError, RuntimeError)):
        store.create(record)


@pytest.mark.parametrize("to", ["confirmed", "cancelled"])
def test_the_controls_answer_is_taken_once(store: Confirmations, to: str) -> None:
    created(store)
    later = AT + timedelta(seconds=30)

    assert store.answer(ID, to, BOUND, later) is None
    assert store.answer(ID, to, BOUND, later) == "not_pending"
    record = held(store)
    assert record["status"] == to
    assert ("ended_by" in record) is (to == "cancelled")


@pytest.mark.parametrize(
    ("bound", "at", "why"),
    [
        ({"thread_key": "22222222-2222-5333-8444-555555555555"}, AT, "not_pending"),
        ({"sub": "b41c9e27-6b3d-4f58-9e12-7c0d8b5a3f64"}, AT, "not_pending"),
        ({"origin_jti": "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50"}, AT, "other_sign_in"),
        ({}, AT + LIMIT, "expired"),
    ],
    ids=["another_thread", "another_user", "another_sign_in", "past_its_limit"],
)
def test_an_answer_the_record_cant_take_changes_nothing_and_says_why(
    store: Confirmations, bound: dict[str, str], at: datetime, why: str
) -> None:
    created(store)
    before = held(store)

    assert store.answer(ID, "confirmed", {**BOUND, **bound}, at) == why
    assert held(store) == before


def test_an_answer_to_a_confirmation_that_doesnt_exist_is_refused(
    store: Confirmations,
) -> None:
    assert store.answer(ID, "confirmed", BOUND, AT) == "not_pending"


@pytest.mark.parametrize("cause", ["message", "time_limit", "session_end"])
def test_only_a_pending_confirmation_lapses(store: Confirmations, cause: str) -> None:
    created(store)

    assert store.lapse(ID, cause, AT) is True
    assert store.lapse(ID, cause, AT) is False
    assert store.answer(ID, "confirmed", BOUND, AT) == "not_pending"
    record = held(store)
    assert (record["status"], record["ended_by"]) == ("lapsed", cause)


def test_a_confirmed_record_never_lapses(store: Confirmations) -> None:
    created(store)
    store.answer(ID, "confirmed", BOUND, AT)

    assert store.lapse(ID, "message", AT) is False
    assert held(store)["status"] == "confirmed"
