"""
Writes a turn's entries to the execution record (ADR-0004, A turn, end to end; OPS-02). Each entry is checked against
its contract before it is put, on the condition that its key is new, so the record is appended to and never rewritten.
Entries are keyed by the sign-in, then by the turn's start, the turn, and the entry's sequence, and expire after 90
days (OPS-10).
"""

import asyncio
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, TypedDict

from jsonschema import ValidationError

from faro.agent import metrics
from faro.contracts import validator

RETENTION = timedelta(days=90)


def wall_time(at: datetime) -> str:
    return at.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RecordError(RuntimeError):
    """
    Names where an entry broke its contract, never the value, which may be a customer's text (POL-11).
    """

    def __init__(self, kind: str, error: ValidationError) -> None:
        path = "".join(f"/{p}" for p in error.absolute_path) or "/"
        super().__init__(f"a {kind} entry breaks {error.validator} at {path}")


class Cited(TypedDict):
    """
    A tool call a handoff may cite, with the turn it is recorded in and the sign-in that made it (ADR-0004, The
    handoff).
    """

    call_id: str
    tool: str
    called_at: str
    outcome: str
    turn: str
    sign_in: str
    result: dict[str, object] | None


class RecordStore(Protocol):
    def put(self, entry: dict[str, Any]) -> None: ...


class DynamoRecords:
    def __init__(self, table: Any) -> None:
        self.table = table

    def put(self, entry: dict[str, Any]) -> None:
        self.table.put_item(
            Item=json.loads(json.dumps(entry), parse_float=Decimal),
            ConditionExpression="attribute_not_exists(entry_key)",
        )


class Turn:
    def __init__(
        self,
        store: RecordStore,
        sign_in: str,
        source: str,
        started: datetime,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.sign_in = sign_in
        self.source = source
        self.started = started
        self.turn_id = str(uuid.uuid4())
        self.now = now
        self.seq = 0
        self.model_calls = 0
        self.tool_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd: float | None = 0.0
        self.decisions: list[dict[str, Any]] = []
        self.cited: list[Cited] = []

    @property
    def prefix(self) -> str:
        """
        What begins each of the turn's entry keys, by which a handoff names the turns its evidence is in.
        """
        return f"{wall_time(self.started)}#{self.turn_id}"

    def drain(self) -> list[Cited]:
        """
        The tool calls made since the last drain, which a node hands to the graph's state as evidence a handoff may cite.
        """
        drained, self.cited = self.cited, []
        return drained

    def decide(self, **fields: Any) -> None:
        """
        One per request the turn served, in order (POL-05), held until the run ends: the decisions are the last entries
        before turn_closed, and an interrupt's entry, which only the entrypoint sees, comes before them.
        """
        self.decisions.append(fields)

    def emit(self, metric: str, **fields: Any) -> None:
        """
        A JSON line for the alarms, with the turn's source and ID.
        """
        metrics.emit(metric, self.source, self.turn_id, **fields)

    async def write(self, kind: str, **fields: Any) -> dict[str, Any]:
        """
        last says, for a tool call, that no attempt follows it; the entry doesn't record it, its metric line does.
        """
        last = bool(fields.pop("last", True))
        at = self.now()
        entry = {
            "sign_in": self.sign_in,
            "entry_key": f"{wall_time(self.started)}#{self.turn_id}#{self.seq:04d}",
            "turn_id": self.turn_id,
            "seq": self.seq,
            "kind": kind,
            "at": wall_time(at),
            "source": self.source,
            "expires_at": int((at + RETENTION).timestamp()),
            **fields,
        }
        error = next(validator("execution-record").iter_errors(entry), None)
        if error is not None:
            raise RecordError(kind, error)
        await asyncio.to_thread(self.store.put, entry)
        self.seq += 1
        self.count(entry)
        fields_of_line = metrics.entry_line(entry, last)
        if fields_of_line is not None:
            self.emit(kind, **fields_of_line)
        return entry

    def count(self, entry: dict[str, Any]) -> None:
        if entry["kind"] == "tool_call":
            self.tool_calls += 1
        if entry["kind"] != "model_call":
            return
        self.model_calls += 1
        self.input_tokens += entry["usage"]["input_tokens"] or 0
        self.output_tokens += entry["usage"]["output_tokens"] or 0
        if self.cost_usd is not None and entry["cost_usd"] is not None:
            self.cost_usd += entry["cost_usd"]
        else:
            self.cost_usd = None

    def totals(self) -> dict[str, Any]:
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": None if self.cost_usd is None else round(self.cost_usd, 8),
        }

    def latency_ms(self) -> int:
        return max(0, round((self.now() - self.started).total_seconds() * 1000))
