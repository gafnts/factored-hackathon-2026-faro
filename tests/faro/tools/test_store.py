"""
The DynamoDB store reads one partition, names every attribute it asks for, and never names is_fraud, so every request
it makes is one the read tools' role allows, on the table and on the by_card index (ADR-0004's amendment of
2026-09-29; POL-40, SEC-05, CTL-04).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_dynamodb import DynamoDBClient

from faro.contracts import schema
from faro.export.items import attribute
from faro.tools.store import CARD, TRANSACTION, DynamoData, MemoryData

from .conftest import OTHER, OWN, example_items

TABLE = "banking-agent-local-tools-data-b3b8b248f604ef9a-5e1a9c3b7d2f4a68"
LATER = "2026-06-15 00:00:00#TRX-EXAMPLE0000000000009"
EARLIER = "2026-06-14 00:00:00#TRX-EXAMPLE0000000000009"
KINDS = ("metadata_item", "customer_item", "card_item", "transaction_item")


def readable() -> set[str]:
    definitions = schema("tools-data")["$defs"]
    names = {name for kind in KINDS for name in definitions[kind]["properties"]}
    assert "is_fraud" in names
    return names - {"is_fraud"}


class Recorder:
    """
    Passes calls to the client and keeps each request, optionally a page of one item at a time.
    """

    def __init__(self, client: DynamoDBClient, *, page: int | None = None) -> None:
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
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("pk", "sk", "card_key", "listed_at")
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "by_card",
                    "KeySchema": [
                        {"AttributeName": "card_key", "KeyType": "HASH"},
                        {"AttributeName": "listed_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        for item in example_items():
            dynamodb.put_item(
                TableName=TABLE, Item={k: attribute(v) for k, v in item.items()}
            )
        yield dynamodb


def test_it_answers_as_the_in_memory_store_does(client: DynamoDBClient) -> None:
    dynamo, memory = DynamoData(client, TABLE), MemoryData(example_items())

    assert dynamo.metadata() == memory.metadata()
    for customer in (OWN, OTHER, "CLI-EXAMPLE00404"):
        assert dynamo.customer(customer) == memory.customer(customer)
        assert sorted(dynamo.cards(customer), key=str) == sorted(
            memory.cards(customer), key=str
        )
    assert dynamo.card(OWN, "PRD-EXAMPLE00002") == memory.card(OWN, "PRD-EXAMPLE00002")
    assert dynamo.card(OWN, "PRD-EXAMPLE00008") is None
    for before in (None, LATER, EARLIER):
        for customer, card in ((OWN, "PRD-EXAMPLE00002"), (OTHER, "PRD-EXAMPLE00002")):
            assert dynamo.transactions(
                customer, card, before, 11
            ) == memory.transactions(customer, card, before, 11)


def test_a_transaction_read_returns_what_find_transactions_uses_without_is_fraud(
    client: DynamoDBClient,
) -> None:
    found = DynamoData(client, TABLE).transactions(OWN, "PRD-EXAMPLE00002", None, 11)

    assert len(found) == 1
    assert set(found[0]) == set(TRANSACTION)
    assert "is_fraud" not in TRANSACTION


def test_a_card_read_returns_only_what_the_card_tools_use(
    client: DynamoDBClient,
) -> None:
    card = DynamoData(client, TABLE).card(OWN, "PRD-EXAMPLE00002")

    assert card is not None
    assert set(card) == set(CARD)


def test_every_request_names_its_attributes_and_none_is_is_fraud(
    client: DynamoDBClient,
) -> None:
    recorder = Recorder(client)
    data = DynamoData(recorder, TABLE)  # type: ignore[arg-type]

    data.metadata()
    data.customer(OWN)
    data.cards(OWN)
    data.card(OWN, "PRD-EXAMPLE00002")
    data.transactions(OWN, "PRD-EXAMPLE00002", None, 11)
    data.transactions(OWN, "PRD-EXAMPLE00002", LATER, 11)

    assert [name for name, _ in recorder.requests] == [
        "get_item",
        "get_item",
        "query",
        "get_item",
        "query",
        "query",
    ]
    assert [r.get("IndexName") for _, r in recorder.requests[-2:]] == ["by_card"] * 2
    for _, request in recorder.requests:
        named = set(request["ExpressionAttributeNames"].values())
        projected = {
            request["ExpressionAttributeNames"][alias.strip()]
            for alias in request["ProjectionExpression"].split(",")
        }
        assert projected
        assert named <= readable()
        assert "Select" not in request


def test_cards_are_read_page_by_page_and_no_transaction_is_read(
    client: DynamoDBClient,
) -> None:
    recorder = Recorder(client, page=1)

    cards = DynamoData(recorder, TABLE).cards(OWN)  # type: ignore[arg-type]

    assert len(cards) == 4
    assert len(recorder.requests) >= 4
    assert all("transaction_id" not in card for card in cards)


def test_the_metadata_is_read_once(client: DynamoDBClient) -> None:
    recorder = Recorder(client)
    data = DynamoData(recorder, TABLE)  # type: ignore[arg-type]

    assert data.metadata() == data.metadata()

    assert len(recorder.requests) == 1
