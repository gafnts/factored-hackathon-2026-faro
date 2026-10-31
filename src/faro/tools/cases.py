"""
The handoff cases table as file_handoff writes it and the console reads it (ADR-0007, Handoffs as cases, and its
amendments of 2026-09-30): a case saved as a draft and later filed, and a reference item, put in the transaction that
files its case, so no two cases share a reference and the console finds a case by its reference. Every write is conditional: a case passes from draft to filed once, and never to another customer.
Transactions' is_fraud is read here too, from each transaction's own item, which only file_handoff's role may read
whole (POL-40). The in-memory stores serve the tests with the same logic the Lambda runs.
"""

import json
import secrets
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Protocol

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from faro.tools.store import Record

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

# Crockford's base32: no I, L, O, or U (ADR-0007).
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
REFERENCE = "REF#"
RANKS = {"urgent": 1, "normal": 0}

Filed = Literal["filed", "case_taken", "reference_taken"]


def queue_order(priority: str, filed_at: str) -> str:
    """
    The queue index's sort key: read in descending order, urgent cases come first, and each priority's newest first.
    """
    return f"{RANKS[priority]}#{filed_at}"


def draw_reference() -> str:
    drawn = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{drawn[:4]}-{drawn[4:]}"


def reference_item(reference: str, handoff_id: str, expires_at: int) -> Record:
    return {
        "pk": f"{REFERENCE}{reference}",
        "kind": "reference",
        "reference": reference,
        "handoff_id": handoff_id,
        "expires_at": expires_at,
    }


class Cases(Protocol):
    def case(self, handoff_id: str) -> Record | None: ...

    def save_draft(self, item: Record) -> bool:
        """
        False when the handoff ID holds a filed case, or another customer's draft.
        """
        ...

    def file(self, case: Record, reference: Record) -> Filed: ...


class FraudFlags(Protocol):
    def is_fraud(self, customer_id: str, transaction_id: str) -> bool | None: ...


def _dynamo(value: Any) -> Any:
    # Amounts arrive as floats, which DynamoDB's serializer refuses.
    return json.loads(json.dumps(value), parse_float=Decimal)


def plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


class DynamoCases:
    def __init__(self, client: "DynamoDBClient", table: str) -> None:
        self._client = client
        self._table = table
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def _item(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {k: self._serializer.serialize(v) for k, v in _dynamo(record).items()}

    def case(self, handoff_id: str) -> Record | None:
        item = self._client.get_item(
            TableName=self._table, Key={"pk": {"S": handoff_id}}, ConsistentRead=True
        ).get("Item")
        if item is None:
            return None
        found: Record = plain(
            {k: self._deserializer.deserialize(v) for k, v in item.items()}
        )
        return found

    def holder(self, reference: str) -> str | None:
        """
        The handoff ID whose case took the reference, read strongly consistent, so a case filed a moment ago is found.
        """
        item = self._client.get_item(
            TableName=self._table,
            Key={"pk": {"S": f"{REFERENCE}{reference}"}},
            ConsistentRead=True,
            ProjectionExpression="handoff_id",
        ).get("Item")
        return None if item is None else item["handoff_id"]["S"]

    def save_draft(self, item: Record) -> bool:
        try:
            self._client.put_item(
                TableName=self._table,
                Item=self._item(item),
                ConditionExpression=(
                    "attribute_not_exists(pk) OR (#status = :draft AND #customer_id = :customer_id)"
                ),
                ExpressionAttributeNames={
                    "#status": "status",
                    "#customer_id": "customer_id",
                },
                ExpressionAttributeValues={
                    ":draft": {"S": "draft"},
                    ":customer_id": {"S": item["customer_id"]},
                },
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def file(self, case: Record, reference: Record) -> Filed:
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._table,
                            "Item": self._item(case),
                            "ConditionExpression": (
                                "attribute_not_exists(pk) OR (#status = :draft AND #customer_id = :customer_id)"
                            ),
                            "ExpressionAttributeNames": {
                                "#status": "status",
                                "#customer_id": "customer_id",
                            },
                            "ExpressionAttributeValues": {
                                ":draft": {"S": "draft"},
                                ":customer_id": {"S": case["customer_id"]},
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table,
                            "Item": self._item(reference),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ]
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            reasons = [
                r.get("Code") for r in error.response.get("CancellationReasons", [])
            ]
            if reasons[:1] == ["ConditionalCheckFailed"]:
                return "case_taken"
            if reasons[1:2] == ["ConditionalCheckFailed"]:
                return "reference_taken"
            raise
        return "filed"


class DynamoFlags:
    def __init__(self, client: "DynamoDBClient", table: str) -> None:
        self._client = client
        self._table = table

    def is_fraud(self, customer_id: str, transaction_id: str) -> bool | None:
        item = self._client.get_item(
            TableName=self._table,
            Key={"pk": {"S": customer_id}, "sk": {"S": f"TXN#{transaction_id}"}},
            ProjectionExpression="#is_fraud",
            ExpressionAttributeNames={"#is_fraud": "is_fraud"},
        ).get("Item")
        if item is None or "is_fraud" not in item:
            return None
        return bool(item["is_fraud"]["BOOL"])


class MemoryCases:
    def __init__(self, items: Iterable[Mapping[str, Any]] = ()) -> None:
        self.items = {i["pk"]: plain(_dynamo(i)) for i in items}

    def case(self, handoff_id: str) -> Record | None:
        found = self.items.get(handoff_id)
        return None if found is None else json.loads(json.dumps(found))

    def holder(self, reference: str) -> str | None:
        found = self.items.get(f"{REFERENCE}{reference}")
        return None if found is None else str(found["handoff_id"])

    def _open(self, pk: str, customer_id: str) -> bool:
        held = self.items.get(pk)
        return held is None or (
            held.get("status") == "draft" and held["customer_id"] == customer_id
        )

    def save_draft(self, item: Record) -> bool:
        if not self._open(item["pk"], item["customer_id"]):
            return False
        self.items[item["pk"]] = plain(_dynamo(item))
        return True

    def file(self, case: Record, reference: Record) -> Filed:
        if not self._open(case["pk"], case["customer_id"]):
            return "case_taken"
        if reference["pk"] in self.items:
            return "reference_taken"
        self.items[case["pk"]] = plain(_dynamo(case))
        self.items[reference["pk"]] = plain(_dynamo(reference))
        return "filed"


class MemoryFlags:
    def __init__(self, items: Iterable[Mapping[str, Any]]) -> None:
        self._flags = {
            (i["pk"], i["sk"]): bool(i["is_fraud"]) for i in items if "is_fraud" in i
        }

    def is_fraud(self, customer_id: str, transaction_id: str) -> bool | None:
        return self._flags.get((customer_id, f"TXN#{transaction_id}"))
