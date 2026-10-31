"""
The JSON lines the alarms read (ADR-0004's amendment of 2026-10-01, the alarms as built; OPS-03, EVL-13): one object per
line, apart from the plain log lines, holding the metric's name, the source (demo, evaluation, or unknown before the
token's claims are read), the turn's ID, which joins a line to the execution record (OPS-01), and enumerated fields
alone, never text, a value, or another identifier. Metric filters on the Runtime's log group count them by source.
"""

import json
import logging
from typing import Any

logger = logging.getLogger("faro.metrics")
logger.propagate = False


def configure() -> None:
    """
    The lines carry the message alone, so a JSON filter can read the whole line.
    """
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


def line(metric: str, source: str, turn_id: str | None, **fields: Any) -> str:
    return json.dumps(
        {"metric": metric, "source": source, "turn_id": turn_id, **fields},
        separators=(",", ":"),
    )


def emit(metric: str, source: str, turn_id: str | None, **fields: Any) -> None:
    logger.info(line(metric, source, turn_id, **fields))


def entry_line(entry: dict[str, Any], last: bool) -> dict[str, Any] | None:
    """
    The fields a record entry's line carries, for the kinds a metric reads.
    """
    kind = entry["kind"]
    if kind == "model_call":
        return {k: entry[k] for k in ("node", "attempt", "outcome")}
    if kind == "tool_call":
        error = entry.get("error") or {}
        return {
            "tool": entry["tool"],
            "attempt": entry["attempt"],
            "outcome": entry["outcome"],
            "error": error.get("code"),
            "planned": bool(error.get("planned")),
            "last": last,
        }
    if kind == "reply_check":
        return {k: entry[k] for k in ("fell_back", "failures")}
    if kind == "handoff":
        return {k: entry[k] for k in ("reason_code", "trigger", "status", "flagged")}
    if kind == "turn_closed":
        return {k: entry[k] for k in ("outcome", "latency_ms")}
    if kind == "request_refused":
        return {"code": entry["code"]}
    return None
