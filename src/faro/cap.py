"""
The cap on the day's turns and guests (ADR-0004, decision 21, and its amendments of 2026-11-07 and 2026-11-08): one item
of the usage counters table, which the Runtime reads before it counts an invited user's or a visitor's turn, and the
guest entry before it makes a guest. Without --turns or --guests this prints the cap and what was counted against it
this UTC day; with them, it sets the cap, and 0 turns pauses the chat and the guest entry. It changes only a cap that
exists, since Terraform writes it once from turns_cap and guests_cap, so a wrong table is reported rather than given
one; on a cap written before the guests', --guests adds them.
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
from faro.guest import usage as guests_usage
from faro.guest.usage import Cap


class CapError(Exception):
    pass


def read(dynamodb: Any, table: str) -> Cap:
    try:
        return guests_usage.read_cap(dynamodb, table)
    except LookupError as error:
        raise CapError(
            f"{table} holds no cap; apply the environment to write it"
        ) from error


def counted(dynamodb: Any, table: str, at: datetime) -> int:
    key = {"counter": {"S": DAY}, "window": {"S": f"{at.astimezone(UTC):%Y-%m-%d}"}}
    item = dynamodb.get_item(TableName=table, Key=key, ConsistentRead=True).get("Item")
    return int(item["turns"]["N"]) if item else 0


def write(
    dynamodb: Any, table: str, turns: int | None = None, guests: int | None = None
) -> None:
    values = {"turns": turns, "guests": guests}
    named = {name: value for name, value in values.items() if value is not None}
    try:
        dynamodb.update_item(
            TableName=table,
            Key=CAP_KEY,
            UpdateExpression="SET " + ", ".join(f"#{n} = :{n}" for n in named),
            ConditionExpression="attribute_exists(#turns)",
            ExpressionAttributeNames={"#turns": "turns", **{f"#{n}": n for n in named}},
            ExpressionAttributeValues={
                f":{n}": {"N": str(v)} for n, v in named.items()
            },
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        raise CapError(
            f"{table} holds no cap; apply the environment to write it"
        ) from error


def turns_described(turns: int) -> str:
    return "0 (paused)" if turns == 0 else f"{turns} turns a day"


def guests_described(guests: int | None) -> str:
    return "unset" if guests is None else f"{guests} a day"


def at_least_0(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("a cap is 0 or more")
    return parsed


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m faro.cap", description=__doc__)
    parser.add_argument("--stack", type=Path, required=True)
    parser.add_argument("--turns", type=at_least_0)
    parser.add_argument("--guests", type=at_least_0)
    return parser.parse_args(argv)


def run(args: argparse.Namespace, dynamodb: Any, now: datetime) -> str:
    table = judges.stack_outputs(args.stack)["runtime_tables"]["usage_counters"]
    cap = read(dynamodb, table)
    today = (
        f"{counted(dynamodb, table, now)} turns and "
        f"{guests_usage.counted(dynamodb, table, now)} guests counted today (UTC)"
    )
    if args.turns is None and args.guests is None:
        unset = ", so the guest entry makes no user" if cap.guests is None else ""
        return (
            f"The cap is {turns_described(cap.turns)}; the guests' cap is "
            f"{guests_described(cap.guests)}{unset}; {today}."
        )
    write(dynamodb, table, args.turns, args.guests)
    changes = []
    if args.turns is not None:
        changes.append(
            f"the turns' cap was {turns_described(cap.turns)} and is "
            f"{turns_described(args.turns)}"
        )
    if args.guests is not None:
        changes.append(
            f"the guests' cap was {guests_described(cap.guests)} and is "
            f"{guests_described(args.guests)}"
        )
    said = "; ".join(changes)
    return f"{said[0].upper()}{said[1:]}; {today}."


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
