"""
Decision 21's limits, as built (ADR-0004's amendment of 2026-10-01; DSN-01, OPS-08): a sign-in's turns a minute and a
user's turns a day, on the wall clock in UTC, so no sign-in spends a model quota every customer shares and no credential
spends more than a day's cap however often it signs in. A turn counts in both windows in one transaction, each on the
condition that its window is below its limit, so both count or neither, and a refused request counts in neither. Each
window's item expires when the window ends.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from botocore.exceptions import ClientError

TURNS_PER_MINUTE = 10
TURNS_PER_DAY = 500


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

    def windows(self, sign_in: str, sub: str, at: datetime) -> tuple[Window, Window]:
        """
        The sign-in's minute and the user's day, in the order the transaction counts them.
        """
        at = at.astimezone(UTC)
        minute = at.replace(second=0, microsecond=0)
        day = minute.replace(hour=0, minute=0)
        return (
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


LIMITS = Limits()


def refusal(windows: Iterable[Window], full: Iterable[bool]) -> str:
    """
    The day's cap is named when it is reached, since waiting a minute won't lift it.
    """
    reached = [w.refusal for w, is_full in zip(windows, full, strict=True) if is_full]
    return "daily_limit" if "daily_limit" in reached else "rate_limited"


class Usage(Protocol):
    def count(self, sign_in: str, sub: str, at: datetime) -> str | None:
        """
        Counts a turn; the code of the limit that refuses it, if one does.
        """
        ...


class DynamoUsage:
    def __init__(self, client: Any, table: str, limits: Limits = LIMITS) -> None:
        self.client = client
        self.table = table
        self.limits = limits

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

    def count(self, sign_in: str, sub: str, at: datetime) -> str | None:
        windows = self.limits.windows(sign_in, sub, at)
        try:
            self.client.transact_write_items(
                TransactItems=[self._update(w) for w in windows]
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            reasons = error.response.get("CancellationReasons") or []
            full = [r.get("Code") == "ConditionalCheckFailed" for r in reasons]
            if len(full) != len(windows) or not any(full):
                raise
            return refusal(windows, full)
        return None


@dataclass
class MemoryUsage:
    limits: Limits = LIMITS
    turns: dict[tuple[str, str], int] = field(default_factory=dict)

    def count(self, sign_in: str, sub: str, at: datetime) -> str | None:
        windows = self.limits.windows(sign_in, sub, at)
        full = [self.turns.get((w.counter, w.window), 0) >= w.limit for w in windows]
        if any(full):
            return refusal(windows, full)
        for w in windows:
            self.turns[(w.counter, w.window)] = (
                self.turns.get((w.counter, w.window), 0) + 1
            )
        return None
