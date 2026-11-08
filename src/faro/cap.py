"""
The cap on the day's turns (ADR-0004, decision 21, and its amendment of 2026-11-07): one item of the usage counters
table, which the Runtime reads before it counts an invited user's or a visitor's turn. Without --turns this prints the
cap and the turns counted against it this UTC day; with it, it sets the cap, and 0 pauses the chat. It changes only a
cap that exists, since Terraform writes it once from turns_cap, so a wrong table is reported rather than given one.
"""

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from faro import judges
from faro.agent.usage import CAP_KEY, DAY


class CapError(Exception):
    pass


def read(dynamodb: Any, table: str) -> int:
    item = dynamodb.get_item(TableName=table, Key=CAP_KEY, ConsistentRead=True).get(
        "Item"
    )
    if item is None or "N" not in item.get("turns", {}):
        raise CapError(f"{table} holds no cap; apply the environment to write it")
    return int(item["turns"]["N"])


def counted(dynamodb: Any, table: str, at: datetime) -> int:
    key = {"counter": {"S": DAY}, "window": {"S": f"{at.astimezone(UTC):%Y-%m-%d}"}}
    item = dynamodb.get_item(TableName=table, Key=key, ConsistentRead=True).get("Item")
    return int(item["turns"]["N"]) if item else 0


def write(dynamodb: Any, table: str, turns: int) -> None:
    try:
        dynamodb.update_item(
            TableName=table,
            Key=CAP_KEY,
            UpdateExpression="SET #turns = :turns",
            ConditionExpression="attribute_exists(#turns)",
            ExpressionAttributeNames={"#turns": "turns"},
            ExpressionAttributeValues={":turns": {"N": str(turns)}},
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        raise CapError(
            f"{table} holds no cap; apply the environment to write it"
        ) from error


def described(cap: int) -> str:
    return "0 (paused)" if cap == 0 else f"{cap} turns a day"


def turns(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("the cap is 0 or more turns")
    return parsed


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m faro.cap", description=__doc__)
    parser.add_argument("--stack", type=Path, required=True)
    parser.add_argument("--turns", type=turns)
    return parser.parse_args(argv)


def run(args: argparse.Namespace, dynamodb: Any, now: datetime) -> str:
    table = judges.stack_outputs(args.stack)["runtime_tables"]["usage_counters"]
    cap = read(dynamodb, table)
    today = f"{counted(dynamodb, table, now)} counted today (UTC)"
    if args.turns is None:
        return f"The cap is {described(cap)}; {today}."
    write(dynamodb, table, args.turns)
    return f"The cap was {described(cap)} and is {described(args.turns)}; {today}."


def main(argv: Sequence[str] | None = None) -> None:
    args = parse(argv)
    try:
        print(
            run(
                args,
                boto3.client("dynamodb", region_name="us-east-1"),
                datetime.now(UTC),
            )
        )
    except (CapError, judges.JudgesError, ClientError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
