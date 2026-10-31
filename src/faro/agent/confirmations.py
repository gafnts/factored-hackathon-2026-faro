"""
The confirmation record as the Runtime keeps it (ADR-0004, The confirmation; POL-09, POL-36; CTL-02). confirm creates
it, with a conditional put, before the control shows; only the control's answer, from the same thread, user, and
sign-in and before the five-minute limit, brings it to confirmed or cancelled, and typed text can end it but never
confirm it. Every change is conditional, and one that fails says why from the record as it was. block_card uses a
confirmed record up (faro.tools.block_card). The deadline Lambda is deferred (D1), so a record left pending
lapses only when a request finds it, and the table's time to live removes it after 24 hours.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from faro.agent.records import wall_time

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

LIMIT = timedelta(minutes=5)
KEPT = timedelta(hours=24)
# What the control's answer is checked against: the conversation, the user, and the sign-in (POL-09).
BOUND = ("thread_key", "sub", "origin_jti")


def new_record(
    confirmation_id: str,
    bound: Mapping[str, str],
    customer_id: str,
    card_id: str,
    reason: str,
    at: datetime,
) -> dict[str, Any]:
    return {
        "confirmation_id": confirmation_id,
        **{k: bound[k] for k in BOUND},
        "customer_id": customer_id,
        "card_id": card_id,
        "reason": reason,
        "status": "pending",
        "created_at": wall_time(at),
        "expires_at": int((at + LIMIT).timestamp()),
        "ttl": int((at + KEPT).timestamp()),
    }


def refusal(
    held: Mapping[str, Any] | None, bound: Mapping[str, str], at: datetime
) -> str:
    """
    Why the control's answer wasn't taken, read from the record as it was.
    """
    if (
        held is None
        or held["status"] != "pending"
        or any(held[k] != bound[k] for k in ("thread_key", "sub"))
    ):
        return "not_pending"
    if held["origin_jti"] != bound["origin_jti"]:
        return "other_sign_in"
    if int(held["expires_at"]) <= at.timestamp():
        return "expired"
    raise RuntimeError(
        "a conditional update failed on a record that meets its condition"
    )


class Confirmations(Protocol):
    def create(self, record: Mapping[str, Any]) -> None: ...

    def answer(
        self, confirmation_id: str, to: str, bound: Mapping[str, str], at: datetime
    ) -> str | None: ...

    def lapse(self, confirmation_id: str, cause: str, at: datetime) -> bool: ...


class DynamoConfirmations:
    def __init__(self, client: "DynamoDBClient", table: str) -> None:
        self.client = client
        self.table = table
        self.serializer = TypeSerializer()
        self.deserializer = TypeDeserializer()

    def values(self, **values: Any) -> dict[str, Any]:
        return {f":{k}": self.serializer.serialize(v) for k, v in values.items()}

    def create(self, record: Mapping[str, Any]) -> None:
        self.client.put_item(
            TableName=self.table,
            Item={k: self.serializer.serialize(v) for k, v in record.items()},
            ConditionExpression="attribute_not_exists(confirmation_id)",
        )

    def answer(
        self, confirmation_id: str, to: str, bound: Mapping[str, str], at: datetime
    ) -> str | None:
        """
        Takes the control's confirm or cancel, returning None, or the refusal when the record can't take it.
        """
        stamp = "confirmed_at" if to == "confirmed" else "ended_at"
        ended = {} if to == "confirmed" else {"ended_by": "control"}
        sets = ", ".join(f"#{k} = :{k}" for k in ("status", stamp, *ended))
        try:
            self.client.update_item(
                TableName=self.table,
                Key={"confirmation_id": {"S": confirmation_id}},
                UpdateExpression=f"SET {sets}",
                ConditionExpression=(
                    "#status = :pending AND #thread_key = :thread_key AND #sub = :sub"
                    " AND #origin_jti = :origin_jti AND #expires_at > :now"
                ),
                ExpressionAttributeNames={
                    f"#{k}": k for k in ("status", stamp, *ended, *BOUND, "expires_at")
                },
                ExpressionAttributeValues=self.values(
                    status=to,
                    pending="pending",
                    now=int(at.timestamp()),
                    **{stamp: wall_time(at)},
                    **ended,
                    **{k: bound[k] for k in BOUND},
                ),
                ReturnValuesOnConditionCheckFailure="ALL_OLD",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            item: Mapping[str, Any] = error.response.get("Item") or {}  # type: ignore[assignment]
            held = {k: self.deserializer.deserialize(v) for k, v in item.items()}
            return refusal(held or None, bound, at)
        return None

    def lapse(self, confirmation_id: str, cause: str, at: datetime) -> bool:
        try:
            self.client.update_item(
                TableName=self.table,
                Key={"confirmation_id": {"S": confirmation_id}},
                UpdateExpression="SET #status = :lapsed, #ended_by = :cause, #ended_at = :at",
                ConditionExpression="#status = :pending",
                ExpressionAttributeNames={
                    f"#{k}": k for k in ("status", "ended_by", "ended_at")
                },
                ExpressionAttributeValues=self.values(
                    lapsed="lapsed", cause=cause, at=wall_time(at), pending="pending"
                ),
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return False
        return True


class MemoryConfirmations:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    def create(self, record: Mapping[str, Any]) -> None:
        if record["confirmation_id"] in self.records:
            raise RuntimeError("a confirmation with that ID exists")
        self.records[record["confirmation_id"]] = dict(record)

    def answer(
        self, confirmation_id: str, to: str, bound: Mapping[str, str], at: datetime
    ) -> str | None:
        held = self.records.get(confirmation_id)
        if (
            held is None
            or held["status"] != "pending"
            or any(held[k] != bound[k] for k in BOUND)
            or held["expires_at"] <= at.timestamp()
        ):
            return refusal(held, bound, at)
        if to == "confirmed":
            held.update(status=to, confirmed_at=wall_time(at))
        else:
            held.update(status=to, ended_by="control", ended_at=wall_time(at))
        return None

    def lapse(self, confirmation_id: str, cause: str, at: datetime) -> bool:
        held = self.records.get(confirmation_id)
        if held is None or held["status"] != "pending":
            return False
        held.update(status="lapsed", ended_by=cause, ended_at=wall_time(at))
        return True
