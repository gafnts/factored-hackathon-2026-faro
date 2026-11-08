"""
Decision 21's limits, as built (ADR-0004's amendments of 2026-10-01 and 2026-11-07; DSN-01, OPS-08): a sign-in's turns
a minute, a user's turns a day, and the day's turns of invited users and visitors together, on the wall clock in UTC, so
no sign-in spends a model quota every customer shares, no credential spends more than a user's day however often it
signs in, and no number of users spends more than the day's cap. A turn counts in each of its windows in one
transaction, each on the condition that its window is below its limit, so all count or none, and a refused request
counts in none. Each window's item expires when the window ends. The cap is one item an administrator sets; while it is
0, an invited user's or a visitor's request is refused before anything is counted. The evaluation's turns count against
the first two limits alone.
"""

import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from botocore.exceptions import ClientError

TURNS_PER_MINUTE = 10
TURNS_PER_DAY = 40
CAPPED = ("demo", "visitor")
CAP_KEY = {"counter": {"S": "cap"}, "window": {"S": "current"}}
DAY = "day"
# Named in this order when several are reached, so the cap's alarm counts every turn it refuses, and a minute's wait is
# offered only when it would help.
REFUSALS = ("global_limit", "daily_limit", "rate_limited")
# Every capped turn writes the day's item, so turns at the same moment can cancel each other's transaction.
ATTEMPTS = 3
BACKOFF_S = 0.05


@dataclass(frozen=True)
class Window:
    counter: str
    window: str
    limit: int
    ends: datetime
    refusal: str


@dataclass(frozen=True)
class Limits:
    per_minute: int = TURNS_PER_MINUTE
    per_day: int = TURNS_PER_DAY

    def windows(
        self, sign_in: str, sub: str, at: datetime, cap: int | None = None
    ) -> tuple[Window, ...]:
        """
        The sign-in's minute, the user's day, and, under a cap, the day of every capped turn, in the order the
        transaction counts them.
        """
        at = at.astimezone(UTC)
        minute = at.replace(second=0, microsecond=0)
        day = minute.replace(hour=0, minute=0)
        windows = (
            Window(
                f"sign_in#{sign_in}",
                minute.strftime("%Y-%m-%dT%H:%M"),
                self.per_minute,
                minute + timedelta(minutes=1),
                "rate_limited",
            ),
            Window(
                f"user#{sub}",
                day.strftime("%Y-%m-%d"),
                self.per_day,
                day + timedelta(days=1),
                "daily_limit",
            ),
        )
        if cap is None:
            return windows
        return (
            *windows,
            Window(
                DAY,
                day.strftime("%Y-%m-%d"),
                cap,
                day + timedelta(days=1),
                "global_limit",
            ),
        )


LIMITS = Limits()


def refusal(windows: Iterable[Window], full: Iterable[bool]) -> str:
    reached = {w.refusal for w, is_full in zip(windows, full, strict=True) if is_full}
    return next(code for code in REFUSALS if code in reached)


class Usage(Protocol):
    def count(self, sign_in: str, sub: str, source: str, at: datetime) -> str | None:
        """
        Counts a turn; the code of the limit that refuses it, or paused, if one does.
        """
        ...


def cancelled(error: ClientError, windows: int) -> list[str]:
    """
    Each item's reason when the transaction was cancelled; any other error is raised again.
    """
    if error.response["Error"]["Code"] != "TransactionCanceledException":
        raise error
    reasons = error.response.get("CancellationReasons") or []
    codes = [r.get("Code", "None") for r in reasons]
    if len(codes) != windows:
        raise error
    return codes


class DynamoUsage:
    def __init__(
        self,
        client: Any,
        table: str,
        limits: Limits = LIMITS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.table = table
        self.limits = limits
        self.sleep = sleep

    def cap(self) -> int:
        """
        Read strongly consistent, so a pause holds from the next request. A missing cap fails the request, so the alarm
        on runtime errors says so, where reading it as a pause would refuse every turn in silence.
        """
        item = self.client.get_item(
            TableName=self.table,
            Key=CAP_KEY,
            ConsistentRead=True,
            ProjectionExpression="#turns",
            ExpressionAttributeNames={"#turns": "turns"},
        ).get("Item")
        if item is None or "N" not in item.get("turns", {}):
            raise LookupError(f"{self.table} holds no cap")
        return int(item["turns"]["N"])

    def _update(self, window: Window) -> dict[str, Any]:
        return {
            "Update": {
                "TableName": self.table,
                "Key": {
                    "counter": {"S": window.counter},
                    "window": {"S": window.window},
                },
                "UpdateExpression": "ADD #turns :one SET #expires_at = :ends",
                "ConditionExpression": "attribute_not_exists(#turns) OR #turns < :limit",
                "ExpressionAttributeNames": {
                    "#turns": "turns",
                    "#expires_at": "expires_at",
                },
                "ExpressionAttributeValues": {
                    ":one": {"N": "1"},
                    ":limit": {"N": str(window.limit)},
                    ":ends": {"N": str(int(window.ends.timestamp()))},
                },
            }
        }

    def count(self, sign_in: str, sub: str, source: str, at: datetime) -> str | None:
        cap = self.cap() if source in CAPPED else None
        if cap == 0:
            return "paused"
        windows = self.limits.windows(sign_in, sub, at, cap)
        items = [self._update(w) for w in windows]
        attempt = 1
        while True:
            try:
                self.client.transact_write_items(TransactItems=items)
                return None
            except ClientError as error:
                codes = cancelled(error, len(windows))
                full = [c == "ConditionalCheckFailed" for c in codes]
                if any(full):
                    return refusal(windows, full)
                conflicted = "TransactionConflict" in codes and set(codes) <= {
                    "None",
                    "TransactionConflict",
                }
                if not conflicted or attempt == ATTEMPTS:
                    raise
            self.sleep(random.uniform(0, BACKOFF_S * attempt))
            attempt += 1


@dataclass
class MemoryUsage:
    limits: Limits = LIMITS
    cap: int | None = None
    turns: dict[tuple[str, str], int] = field(default_factory=dict)

    def count(self, sign_in: str, sub: str, source: str, at: datetime) -> str | None:
        cap = self.cap if source in CAPPED else None
        if cap == 0:
            return "paused"
        windows = self.limits.windows(sign_in, sub, at, cap)
        full = [self.turns.get((w.counter, w.window), 0) >= w.limit for w in windows]
        if any(full):
            return refusal(windows, full)
        for w in windows:
            self.turns[(w.counter, w.window)] = (
                self.turns.get((w.counter, w.window), 0) + 1
            )
        return None
