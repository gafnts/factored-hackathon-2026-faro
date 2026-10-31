"""
One case, found by its reference (ADR-0007's amendments of 2026-09-30): the reference's own item, then the case, both
read strongly consistent, so a case filed a moment ago is found at once. A draft holds no reference, and an evaluation's
case reads as no case at all (EVL-13). The calls come from the turns the case names in its record, read with a tool
call's attributes only, all the case Lambda's role may name, so no message, reply, or model output reaches the console
(CTL-05). Of each call, the console gets what the record holds about it and the rows of its result that the case's facts
and actions name, read as file_handoff read them when it checked the case (OPS-02).
"""

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from faro.console.queue import SOURCE, Answer
from faro.tools.provenance import KEYS, Entry, Records, rows
from faro.tools.store import Record

REFERENCE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$")
# Every attribute a tool call's entry holds but its input: all the case Lambda's role may name.
CALL = (
    "sign_in",
    "entry_key",
    "kind",
    "call_id",
    "tool",
    "via",
    "attempt",
    "called_at",
    "latency_ms",
    "request_id",
    "outcome",
    "result",
    "error",
)
SHOWN = (
    "reference",
    "status",
    "queue",
    "priority",
    "reason_code",
    "language",
    "saved_at",
    "filed_at",
    "flagged",
    "validation_errors",
    "payload",
)
MAX_ROWS = 60


class CaseBook(Protocol):
    def holder(self, reference: str) -> str | None: ...

    def case(self, handoff_id: str) -> Record | None: ...


def _scalars(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in row.items()
        if v is None or isinstance(v, str | int | float | bool)
    }


def _names(row: Mapping[str, Any], fact: Mapping[str, Any]) -> bool:
    key = KEYS.get(fact["subject"])
    added = (row.get("subject"), row.get("id"), row.get("field")) == (
        fact["subject"],
        fact["id"],
        fact["field"],
    )
    return added or (key is not None and row.get(key) == fact["id"])


def cited_rows(
    result: Any, facts: Sequence[Mapping[str, Any]], cards: Sequence[str]
) -> list[dict[str, Any]]:
    """
    The rows of a call's result that the facts citing it, or the block actions citing it, name: a card or a transaction
    by its ID, a fact file_handoff added by its subject, record, and field, and the customer's own record. Another card
    or transaction the same read returned isn't part of the case.
    """
    found: list[dict[str, Any]] = []
    customer = result.get("customer") if isinstance(result, dict) else None
    if isinstance(customer, dict) and any(f["subject"] == "customer" for f in facts):
        found.append(_scalars(customer))
    for row in rows(result):
        if any(_names(row, f) for f in facts) or row.get("card_id") in cards:
            found.append(_scalars(row))
    unique = {json.dumps(r, sort_keys=True): r for r in found if r}
    return list(unique.values())[:MAX_ROWS]


def recorded_call(
    call_id: str, entry: Entry | None, payload: Mapping[str, Any]
) -> dict[str, Any]:
    if entry is None:
        return {"call_id": call_id, "recorded": False}
    shown = {
        "call_id": call_id,
        "recorded": True,
        **{
            k: entry[k]
            for k in (
                "tool",
                "via",
                "attempt",
                "called_at",
                "latency_ms",
                "request_id",
                "outcome",
            )
        },
    }
    if entry["outcome"] in ("failed", "denied"):
        return {**shown, "error_code": entry["error"]["code"], "rows": []}
    facts = [f for f in payload["verified_facts"] if f["evidence"] == call_id]
    cards = [a["card_id"] for a in payload["actions"] if call_id in a["evidence"]]
    return {**shown, "rows": cited_rows(entry.get("result"), facts, cards)}


def last_attempts(entries: Sequence[Entry]) -> dict[str, Entry]:
    calls: dict[str, Entry] = {}
    for entry in entries:
        if entry.get("kind") != "tool_call":
            continue
        held = calls.get(entry["call_id"])
        if held is None or entry["attempt"] >= held["attempt"]:
            calls[entry["call_id"]] = entry
    return calls


def read_case(book: CaseBook, records: Records, reference: Any) -> Answer:
    if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
        return 400, {"error": "invalid_request"}
    handoff_id = book.holder(reference)
    case = book.case(handoff_id) if handoff_id is not None else None
    if (
        case is None
        or case.get("kind") != "case"
        or case.get("status") == "draft"
        or case.get("source") != SOURCE
        or case.get("reference") != reference
    ):
        return 404, {"error": "not_found"}
    payload = case["payload"]
    record = case["record"]
    calls = last_attempts(records.turns(record["sign_in"], record["turns"], CALL))
    return 200, {
        "case": {k: case[k] for k in SHOWN if k in case},
        "calls": [
            recorded_call(e["call_id"], calls.get(e["call_id"]), payload)
            for e in payload["evidence"]
        ],
    }
