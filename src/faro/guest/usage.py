"""
The guests the guest entry makes in a UTC day (ADR-0004, decision 21; ADR-0007, The guest entry, How many it makes):
a key of the usage counters table for each day, beside the turns' keys, counted on the condition that it's below the
cap item's guests, and before the user is made, so a failure after it spends a guest and never makes one past the
cap. While the cap item's turns are 0 the chat is paused, and the guest entry with it. A cap item with no guests
predates the guests' cap, so the guest entry makes no user until make cap writes one.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from botocore.exceptions import ClientError

# faro.agent.usage's key, which the Runtime's zip carries and this one doesn't.
CAP_KEY = {"counter": {"S": "cap"}, "window": {"S": "current"}}
GUESTS = "guests"

Admission = Literal["counted", "full", "paused"]


@dataclass(frozen=True)
class Cap:
    turns: int
    guests: int | None


def read_cap(client: Any, table: str) -> Cap:
    """
    Read strongly consistent, so a pause holds from the next click; a missing cap fails the click.
    """
    item = client.get_item(
        TableName=table,
        Key=CAP_KEY,
        ConsistentRead=True,
        ProjectionExpression="#turns, #guests",
        ExpressionAttributeNames={"#turns": "turns", "#guests": GUESTS},
    ).get("Item")
    if item is None or "N" not in item.get("turns", {}):
        raise LookupError(f"{table} holds no cap")
    guests = item.get(GUESTS, {}).get("N")
    return Cap(int(item["turns"]["N"]), None if guests is None else int(guests))


def day_of(at: datetime) -> tuple[str, int]:
    """
    The UTC day's key, and the instant it ends, when its count expires.
    """
    day = at.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return day.strftime("%Y-%m-%d"), int((day + timedelta(days=1)).timestamp())


def admit(client: Any, table: str, at: datetime) -> Admission:
    cap = read_cap(client, table)
    if cap.turns == 0:
        return "paused"
    if cap.guests is None:
        raise LookupError(f"{table}'s cap holds no guests; run make cap GUESTS=")
    # The condition lets a day's first guest through whatever the cap, since its count doesn't exist yet.
    if cap.guests == 0:
        return "full"
    day, ends = day_of(at)
    try:
        client.update_item(
            TableName=table,
            Key={"counter": {"S": GUESTS}, "window": {"S": day}},
            UpdateExpression="ADD #guests :one SET #expires_at = :ends",
            ConditionExpression="attribute_not_exists(#guests) OR #guests < :cap",
            ExpressionAttributeNames={"#guests": GUESTS, "#expires_at": "expires_at"},
            ExpressionAttributeValues={
                ":one": {"N": "1"},
                ":cap": {"N": str(cap.guests)},
                ":ends": {"N": str(ends)},
            },
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return "full"
    return "counted"


def counted(client: Any, table: str, at: datetime) -> int:
    day, _ = day_of(at)
    item = client.get_item(
        TableName=table,
        Key={"counter": {"S": GUESTS}, "window": {"S": day}},
        ConsistentRead=True,
    ).get("Item")
    return int(item[GUESTS]["N"]) if item else 0
