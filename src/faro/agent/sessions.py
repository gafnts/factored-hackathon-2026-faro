"""
Binds a runtime session to the user who opened it (ADR-0004, Threads and runtime sessions; decision 12). AgentCore
checks a session ID's format, not its owner, so the first request records its sub with a conditional put, and every
later one is refused unless its sub is the same. A binding lasts as long as its runtime session can, and one that has
expired can be bound again.
"""

from datetime import datetime, timedelta
from typing import Any, Protocol

from botocore.exceptions import ClientError

from faro.agent.records import wall_time

LIFETIME = timedelta(hours=8)


class Bindings(Protocol):
    def bind(self, session_id: str, sub: str, at: datetime) -> bool: ...


class DynamoBindings:
    def __init__(self, client: Any, table: str) -> None:
        self.client = client
        self.table = table

    def bind(self, session_id: str, sub: str, at: datetime) -> bool:
        try:
            self.client.put_item(
                TableName=self.table,
                Item={
                    "runtime_session_id": {"S": session_id},
                    "sub": {"S": sub},
                    "bound_at": {"S": wall_time(at)},
                    "expires_at": {"N": str(int((at + LIFETIME).timestamp()))},
                },
                ConditionExpression="attribute_not_exists(runtime_session_id) OR expires_at < :now",
                ExpressionAttributeValues={":now": {"N": str(int(at.timestamp()))}},
                ReturnValuesOnConditionCheckFailure="ALL_OLD",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            item: Any = error.response.get("Item", {})
            held: str | None = item.get("sub", {}).get("S")
            return held == sub
        return True
