"""
The sandbox in DynamoDB answers as the in-memory one does: its overlay reads strongly consistently, so a read-back sees
the write before it, and reads one sign-in's items only; and DynamoDB itself checks every change to a confirmation, so a
block uses it up in one transaction with its first write, or writes nothing (ADR-0004, Stores and The confirmation;
POL-33, POL-36, POL-37; CTL-02).
"""

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_dynamodb import DynamoDBClient

from faro.export.items import attribute
from faro.tools.block_card import block_card
from faro.tools.sandbox import (
    BlockStores,
    DynamoOverlay,
    DynamoSandbox,
    MemoryOverlay,
    MemorySandbox,
)
from faro.tools.store import MemoryData

from .conftest import OTHER, OWN, SIGN_IN, example_items, overlay_item
from .test_block_card import CALL, CONFIRMATION, NOW, confirmation
from .test_fixtures import fixture_card

OVERLAY = "banking-agent-local-sandbox-overlay"
CONFIRMATIONS = "banking-agent-local-confirmations"
ELSEWHERE = "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50"
ITEMS = [
    overlay_item("PRD-EXAMPLE00002"),
    overlay_item("PRD-EXAMPLE00005"),
    overlay_item("PRD-EXAMPLE00007", sign_in=ELSEWHERE),
    overlay_item("PRD-EXAMPLE00008", customer_id=OTHER),
]


class Recorder:
    def __init__(self, client: DynamoDBClient, page: int | None = None) -> None:
        self.client = client
        self.page = page
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def get_item(self, **request: Any) -> Any:
        self.requests.append(("get_item", request))
        return self.client.get_item(**request)

    def query(self, **request: Any) -> Any:
        self.requests.append(("query", request))
        if self.page:
            return self.client.query(**request, Limit=self.page)
        return self.client.query(**request)

    def __getattr__(self, name: str) -> Any:
        def recorded(**request: Any) -> Any:
            self.requests.append((name, request))
            return getattr(self.client, name)(**request)

        return recorded


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
            TableName=OVERLAY,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("sign_in", "item")
            ],
            KeySchema=[
                {"AttributeName": "sign_in", "KeyType": "HASH"},
                {"AttributeName": "item", "KeyType": "RANGE"},
            ],
        )
        dynamodb.create_table(
            TableName=CONFIRMATIONS,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "confirmation_id", "AttributeType": "S"}
            ],
            KeySchema=[{"AttributeName": "confirmation_id", "KeyType": "HASH"}],
        )
        for item in ITEMS:
            dynamodb.put_item(
                TableName=OVERLAY, Item={k: attribute(v) for k, v in item.items()}
            )
        yield dynamodb


def held(client: DynamoDBClient, **fields: Any) -> dict[str, Any]:
    record = confirmation(**fields)
    client.put_item(
        TableName=CONFIRMATIONS, Item={k: attribute(v) for k, v in record.items()}
    )
    return record


def empty_overlay(client: DynamoDBClient) -> None:
    for item in ITEMS:
        client.delete_item(
            TableName=OVERLAY,
            Key={"sign_in": {"S": item["sign_in"]}, "item": {"S": item["item"]}},
        )


def test_the_overlay_answers_as_the_in_memory_one_does(client: DynamoDBClient) -> None:
    dynamo, memory = DynamoOverlay(client, OVERLAY), MemoryOverlay(ITEMS)

    for sign_in in (SIGN_IN, ELSEWHERE):
        for customer in (OWN, OTHER):
            assert dynamo.statuses(sign_in, customer) == memory.statuses(
                sign_in, customer
            )
            for card in ("PRD-EXAMPLE00002", "PRD-EXAMPLE00007", "PRD-EXAMPLE00008"):
                assert dynamo.status(sign_in, customer, card) == memory.status(
                    sign_in, customer, card
                )
    assert dynamo.statuses(SIGN_IN, OWN) == {
        "PRD-EXAMPLE00002": "Blocked",
        "PRD-EXAMPLE00005": "Blocked",
    }


def test_every_read_is_strongly_consistent_and_read_page_by_page(
    client: DynamoDBClient,
) -> None:
    recorder = Recorder(client, page=1)
    overlay = DynamoOverlay(recorder, OVERLAY)  # type: ignore[arg-type]

    assert len(overlay.statuses(SIGN_IN, OWN)) == 2
    overlay.status(SIGN_IN, OWN, "PRD-EXAMPLE00002")

    assert len(recorder.requests) >= 3
    assert all(request["ConsistentRead"] for _, request in recorder.requests)
    assert all(
        request.get("ExpressionAttributeValues", {}).get(":sign_in", {"S": SIGN_IN})
        == {"S": SIGN_IN}
        for _, request in recorder.requests
    )


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"status": "pending"},
        {
            "status": "consumed",
            "attempts": 1,
            "outcome": "verified",
            "read_back": "Blocked",
        },
        {"card_id": "PRD-EXAMPLE00007"},
    ],
    ids=["confirmed", "pending", "used", "closed_card"],
)
def test_a_block_answers_as_it_does_in_memory(
    client: DynamoDBClient, fields: dict[str, Any]
) -> None:
    empty_overlay(client)
    record = held(client, **fields)
    dynamo = DynamoSandbox(client, OVERLAY, CONFIRMATIONS)
    memory = MemorySandbox([record])
    call = {**CALL, "card_id": record["card_id"]}

    answered = block_card(BlockStores(MemoryData(example_items()), dynamo), call, NOW)
    expected = block_card(BlockStores(MemoryData(example_items()), memory), call, NOW)

    assert answered == expected
    assert dynamo.confirmation(CONFIRMATION) == memory.confirmation(CONFIRMATION)
    assert dynamo.statuses(SIGN_IN, OWN) == memory.statuses(SIGN_IN, OWN)


@pytest.mark.parametrize("card_id", ["PRD-EXAMPLE00002", "PRD-FIXTUREA0001"])
def test_a_block_reads_the_overlay_by_key_alone(
    client: DynamoDBClient, card_id: str
) -> None:
    """
    The block's role may get and put the overlay's items, never query them.
    """
    empty_overlay(client)
    fixture = {**fixture_card(), "product_status": "Active"}
    client.put_item(
        TableName=OVERLAY,
        Item={
            k: attribute(v)
            for k, v in json.loads(json.dumps(fixture), parse_float=Decimal).items()
        },
    )
    held(client, card_id=card_id)
    recorder = Recorder(client)
    sandbox = DynamoSandbox(recorder, OVERLAY, CONFIRMATIONS)  # type: ignore[arg-type]

    answered = block_card(
        BlockStores(MemoryData(example_items()), sandbox),
        {**CALL, "card_id": card_id},
        NOW,
    )

    assert answered["block_outcome"] == "verified"
    assert not [r for r in recorder.requests if r[0] in ("query", "scan")]


def test_a_confirmation_comes_back_with_its_numbers_as_integers(
    client: DynamoDBClient,
) -> None:
    record = held(client, status="consumed", attempts=2)

    read = DynamoSandbox(client, OVERLAY, CONFIRMATIONS).confirmation(CONFIRMATION)

    assert read == record
    assert read is not None and type(read["attempts"]) is int


def test_a_block_that_cant_use_its_confirmation_writes_nothing(
    client: DynamoDBClient,
) -> None:
    empty_overlay(client)
    record = held(client, status="pending")
    sandbox = DynamoSandbox(client, OVERLAY, CONFIRMATIONS)

    assert sandbox.consume({**record, "status": "confirmed"}, NOW) is False
    assert sandbox.statuses(SIGN_IN, OWN) == {}
    assert sandbox.confirmation(CONFIRMATION) == record


def test_a_count_or_an_outcome_another_call_changed_is_never_overwritten(
    client: DynamoDBClient,
) -> None:
    record = held(client, status="consumed", attempts=2)
    sandbox = DynamoSandbox(client, OVERLAY, CONFIRMATIONS)

    assert sandbox.write_again(record, 1, NOW) is False
    assert sandbox.write_again(record, 2, NOW) is True
    assert sandbox.settle(CONFIRMATION, 2, "verified", "Blocked") is False
    assert sandbox.settle(CONFIRMATION, 3, "verified", "Blocked") is True
    assert sandbox.settle(CONFIRMATION, 3, "not_verified", "Active") is False
    final = sandbox.confirmation(CONFIRMATION)
    assert final is not None
    assert (final["attempts"], final["outcome"]) == (3, "verified")
