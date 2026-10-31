"""
A queue lists the demo's cases of one queue and status, urgent first and each priority's newest first, a page at a time,
and never an evaluation's case or a draft (ADR-0007's amendments of 2026-09-30; EVL-13, OPS-08).
"""

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_dynamodb import DynamoDBClient

from faro.console.queue import (
    DynamoQueue,
    MemoryQueue,
    QueueIndex,
    decode,
    encode,
    list_cases,
)
from faro.contracts import validator
from faro.export.items import attribute
from faro.tools.cases import queue_order

CASES = "banking-agent-local-handoff-cases"


def filed(
    n: int,
    priority: str,
    filed_at: str,
    queue: str = "dispute_intake",
    source: str = "demo",
) -> dict[str, Any]:
    return {
        "pk": f"00000000-0000-4000-8000-{n:012d}",
        "kind": "case",
        "reference": f"AAAA-{n:04d}",
        "queue": queue,
        "priority": priority,
        "reason_code": "unrecognized_charge"
        if queue == "dispute_intake"
        else "complaint",
        "language": "es",
        "source": source,
        "status": "filed",
        "queue_key": f"{source}#{queue}#filed",
        "queue_order": queue_order(priority, filed_at),
        "filed_at": filed_at,
        "flagged": n == 3,
        "payload": {"summary": "never in a row"},
    }


ITEMS = [
    filed(1, "normal", "2026-10-02T15:00:00.000Z"),
    filed(2, "urgent", "2026-10-02T14:00:00.000Z"),
    filed(3, "normal", "2026-10-02T16:00:00.000Z"),
    filed(4, "urgent", "2026-10-02T15:30:00.000Z"),
    filed(5, "urgent", "2026-10-02T17:00:00.000Z", source="evaluation"),
    filed(6, "normal", "2026-10-02T17:00:00.000Z", queue="customer_service"),
    # A draft carries no queue_key, so no queue shows it.
    {"pk": "00000000-0000-4000-8000-000000000007", "kind": "case", "status": "draft"},
]
# Urgent first, then each priority's newest first.
ORDER = ["AAAA-0004", "AAAA-0002", "AAAA-0003", "AAAA-0001"]


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
            TableName=CASES,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": n, "AttributeType": "S"}
                for n in ("pk", "queue_key", "queue_order")
            ],
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "by_queue",
                    "KeySchema": [
                        {"AttributeName": "queue_key", "KeyType": "HASH"},
                        {"AttributeName": "queue_order", "KeyType": "RANGE"},
                    ],
                    "Projection": {
                        "ProjectionType": "INCLUDE",
                        "NonKeyAttributes": [
                            "reference",
                            "priority",
                            "reason_code",
                            "language",
                            "filed_at",
                            "flagged",
                        ],
                    },
                }
            ],
        )
        for item in ITEMS:
            dynamodb.put_item(
                TableName=CASES, Item={k: attribute(v) for k, v in item.items()}
            )
        yield dynamodb


def indexes(client: DynamoDBClient) -> list[QueueIndex]:
    return [DynamoQueue(client, CASES), MemoryQueue(ITEMS)]


def listed(index: QueueIndex, **query: str) -> dict[str, Any]:
    status, body = list_cases(index, query)
    assert status == 200
    validator("console", "case_list").validate(body)
    return body


def test_a_queue_lists_urgent_cases_first_and_each_prioritys_newest_first(
    client: DynamoDBClient,
) -> None:
    for index in indexes(client):
        page = listed(index, queue="dispute_intake")

        assert [c["reference"] for c in page["cases"]] == ORDER
        assert (page["status"], page["next_cursor"]) == ("filed", None)


def test_a_row_holds_the_index_projection_only(client: DynamoDBClient) -> None:
    for index in indexes(client):
        row = listed(index, queue="dispute_intake")["cases"][2]

        assert row == {
            "reference": "AAAA-0003",
            "priority": "normal",
            "reason_code": "unrecognized_charge",
            "language": "es",
            "filed_at": "2026-10-02T16:00:00.000Z",
            "flagged": True,
        }


def test_an_evaluations_case_and_a_draft_never_reach_a_queue(
    client: DynamoDBClient,
) -> None:
    for index in indexes(client):
        shown = [
            c["reference"]
            for queue in ("dispute_intake", "customer_service")
            for c in listed(index, queue=queue)["cases"]
        ]

        assert "AAAA-0005" not in shown
        assert sorted(shown) == [
            "AAAA-0001",
            "AAAA-0002",
            "AAAA-0003",
            "AAAA-0004",
            "AAAA-0006",
        ]


def test_pages_follow_one_another_in_order_to_the_last(client: DynamoDBClient) -> None:
    for index in indexes(client):
        seen: list[str] = []
        query = {"queue": "dispute_intake", "limit": "3"}
        while True:
            page = listed(index, **query)
            seen += [c["reference"] for c in page["cases"]]
            if page["next_cursor"] is None:
                break
            query = {**query, "cursor": page["next_cursor"]}

        assert seen == ORDER


def test_a_status_no_case_has_yet_lists_nothing(client: DynamoDBClient) -> None:
    # Claim and resolve are deferred, so every case stays filed (ADR-0007's amendment of 2026-09-30).
    for index in indexes(client):
        assert listed(index, queue="dispute_intake", status="claimed")["cases"] == []


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"queue": "fraud"},
        {"queue": "dispute_intake", "source": "evaluation"},
        {"queue": "dispute_intake", "status": "draft"},
        {"queue": "dispute_intake", "limit": "0"},
        {"queue": "dispute_intake", "limit": "51"},
        {"queue": "dispute_intake", "limit": "ten"},
        {"queue": "dispute_intake,customer_service"},
        {"queue": "dispute_intake", "cursor": "not a cursor"},
    ],
)
def test_a_query_outside_the_contract_is_refused(query: dict[str, str]) -> None:
    assert list_cases(MemoryQueue(ITEMS), query) == (400, {"error": "invalid_request"})


def test_a_missing_query_string_is_refused() -> None:
    assert list_cases(MemoryQueue(ITEMS), None) == (400, {"error": "invalid_request"})


def cursor_of(key: Any) -> str:
    text = json.dumps(key).encode()
    return base64.urlsafe_b64encode(text).decode().rstrip("=")


def test_a_cursor_names_a_key_of_the_asked_for_queue_and_status_only() -> None:
    row = filed(1, "normal", "2026-10-02T15:00:00.000Z")
    cursor = encode(row)
    key = decode(cursor, "demo#dispute_intake#filed")

    assert key == {k: row[k] for k in ("pk", "queue_key", "queue_order")}
    assert decode(cursor, "demo#customer_service#filed") is None
    assert decode(cursor, "evaluation#dispute_intake#filed") is None
    for tampered in (
        {**key, "queue_key": "evaluation#dispute_intake#filed"},
        {**key, "pk": "REF#AAAA-0001"},
        {**key, "queue_order": "2#2026-10-02T15:00:00.000Z"},
        {**key, "reference": "AAAA-0001"},
        [key],
    ):
        assert decode(cursor_of(tampered), "demo#dispute_intake#filed") is None
    assert decode("%%%", "demo#dispute_intake#filed") is None


def test_a_cursor_for_another_queue_is_refused() -> None:
    elsewhere = encode(
        filed(6, "normal", "2026-10-02T17:00:00.000Z", queue="customer_service")
    )

    status, _ = list_cases(
        MemoryQueue(ITEMS), {"queue": "dispute_intake", "cursor": elsewhere}
    )

    assert status == 400
