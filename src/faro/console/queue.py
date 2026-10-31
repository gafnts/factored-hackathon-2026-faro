"""
A page of a queue (ADR-0007's amendments of 2026-09-30): the demo's cases of one queue and status, read from the by_queue
index in descending order of queue_order, so urgent cases come first and each priority's newest first. The source is
never the caller's to choose, so an evaluation's cases never reach a human agent (EVL-13), and the list reads the index's
projection alone, never a payload. The in-memory index serves the tests with the same logic the Lambda runs.
"""

import base64
import binascii
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

from boto3.dynamodb.types import TypeDeserializer

from faro.contracts import validator
from faro.tools.cases import plain
from faro.tools.store import Record

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

SOURCE = "demo"
INDEX = "by_queue"
PAGE = 25
KEYS = ("pk", "queue_key", "queue_order")
ROW = ("reference", "priority", "reason_code", "language", "filed_at", "flagged")
# Every attribute a page names: all the queue Lambda's role may name.
PROJECTED = (*KEYS, *ROW)
ORDER = re.compile(
    r"^[01]#[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
)

Answer = tuple[int, dict[str, Any]]


class QueueIndex(Protocol):
    def page(
        self, key: str, limit: int, start: Mapping[str, str] | None
    ) -> list[Record]:
        """
        Up to limit cases under the key, in the index's descending order, after start's if one is given.
        """
        ...


class DynamoQueue:
    def __init__(self, client: "DynamoDBClient", table: str) -> None:
        self._client = client
        self._table = table
        self._deserializer = TypeDeserializer()

    def page(
        self, key: str, limit: int, start: Mapping[str, str] | None
    ) -> list[Record]:
        alias = {name: f"#a{i}" for i, name in enumerate(PROJECTED)}
        found: list[Record] = []
        paging: dict[str, Any] = (
            {"ExclusiveStartKey": {k: {"S": v} for k, v in start.items()}}
            if start
            else {}
        )
        while len(found) < limit:
            response = self._client.query(
                TableName=self._table,
                IndexName=INDEX,
                KeyConditionExpression=f"{alias['queue_key']} = :key",
                ExpressionAttributeNames={a: name for name, a in alias.items()},
                ExpressionAttributeValues={":key": {"S": key}},
                ProjectionExpression=", ".join(alias.values()),
                ScanIndexForward=False,
                Limit=limit - len(found),
                **paging,
            )
            found += [
                plain({k: self._deserializer.deserialize(v) for k, v in item.items()})
                for item in response.get("Items", [])
            ]
            if "LastEvaluatedKey" not in response:
                break
            paging = {"ExclusiveStartKey": response["LastEvaluatedKey"]}
        return found


class MemoryQueue:
    def __init__(self, items: Iterable[Mapping[str, Any]] = ()) -> None:
        self.items = [dict(item) for item in items]

    def page(
        self, key: str, limit: int, start: Mapping[str, str] | None
    ) -> list[Record]:
        held = sorted(
            (i for i in self.items if i.get("queue_key") == key and "queue_order" in i),
            key=lambda i: (i["queue_order"], i["pk"]),
            reverse=True,
        )
        if start:
            after = (start["queue_order"], start["pk"])
            held = [i for i in held if (i["queue_order"], i["pk"]) < after]
        return [{k: i[k] for k in PROJECTED if k in i} for i in held[:limit]]


def encode(row: Mapping[str, Any]) -> str:
    key = json.dumps({k: row[k] for k in KEYS}, separators=(",", ":"))
    return base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")


def decode(cursor: str, queue_key: str) -> dict[str, str] | None:
    """
    The index key a cursor holds, or None unless it is a key of the asked-for queue and status.
    """
    try:
        key = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, binascii.Error):
        return None
    if (
        not isinstance(key, dict)
        or set(key) != set(KEYS)
        or key["queue_key"] != queue_key
        or not isinstance(key["queue_order"], str)
        or not ORDER.fullmatch(key["queue_order"])
        or not isinstance(key["pk"], str)
    ):
        return None
    try:
        uuid.UUID(key["pk"])
    except ValueError:
        return None
    return key


def list_cases(index: QueueIndex, query: Mapping[str, Any] | None) -> Answer:
    asked = dict(query or {})
    if not validator("console", "list_query").is_valid(asked):
        return 400, {"error": "invalid_request"}
    status = asked.get("status", "filed")
    key = f"{SOURCE}#{asked['queue']}#{status}"
    start = None
    if "cursor" in asked:
        start = decode(asked["cursor"], key)
        if start is None:
            return 400, {"error": "invalid_request"}
    limit = int(asked.get("limit", PAGE))
    # One more than the page shows whether another page follows.
    found = index.page(key, limit + 1, start)
    shown = found[:limit]
    return 200, {
        "queue": asked["queue"],
        "status": status,
        "cases": [{k: row[k] for k in ROW} for row in shown],
        "next_cursor": encode(shown[-1]) if len(found) > limit else None,
    }
