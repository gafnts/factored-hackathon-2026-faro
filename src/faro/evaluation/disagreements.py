"""
The disagreement log (ADR-0005, The oracle, and its amendment of 2026-10-01): where the system and the oracle differ,
the log records the question in writing and, once triaged, whether the oracle, the system, or the policy's wording was
wrong. Entries live in docs/evaluation/disagreements.json and hold rule IDs, enums, tool names, and placeholders, with
the question and its resolution in our words, never a record's value (SEC-03); disagreements.md is generated from them.
CI's regression gate lets a finding through only while an open entry matches it.
"""

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from faro.evaluation import generator

LOG = Path("docs/evaluation/disagreements.json")
PAGE = Path("docs/evaluation/disagreements.md")
STATUSES = ("open", "closed")
VERDICTS = {
    None: "To triage",
    "oracle": "The oracle was wrong",
    "system": "The system was wrong",
    "policy_wording": "The policy's wording was unclear",
}
CHECKS = frozenset(
    {
        "sends",
        "labels",
        "outcome_class",
        "awaiting",
        "tool_required",
        "tool_forbidden",
        "turns",
        "language",
        "fact",
        "extra_figure",
        "withheld",
        "handoff.reason_code",
        "handoff.trigger",
        "handoff.queue",
        "handoff.priority",
        "blocked",
    }
)
ENTRY_ID = re.compile(r"^D-[0-9]{3}$")
RULE = re.compile(r"^POL-[0-9]{2}$")
WORD = re.compile(r"^[a-z_.{}]+$")
# What a question may cite: rules, records, and entries; any other digit could be a record's value.
CITED = re.compile(r"\b(?:POL|ADR|D)-[0-9]+\b|\b20[0-9]{2}-[0-9]{2}-[0-9]{2}\b")
WHERE = ("situation", "language", "turn", "check", "expected", "observed")


def load(path: Path = LOG) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))[
        "entries"
    ]
    return entries


def _value_ok(value: Any) -> bool:
    if isinstance(value, list):
        return all(v is None or (isinstance(v, str) and WORD.match(v)) for v in value)
    return (
        value is None
        or isinstance(value, int)
        or (isinstance(value, str) and bool(WORD.match(value)))
    )


def problems(entries: Sequence[Mapping[str, Any]]) -> list[str]:
    found = []
    ids = [e.get("id") for e in entries]
    if len(set(ids)) != len(ids):
        found.append("entry IDs repeat")
    for entry in entries:
        name = entry.get("id", "?")
        if not ENTRY_ID.match(str(name)):
            found.append(f"{name}: an ID is D- and three digits")
        if entry.get("status") not in STATUSES:
            found.append(f"{name}: status is open or closed")
        if entry.get("verdict") not in VERDICTS:
            found.append(f"{name}: unknown verdict")
        if entry.get("status") == "closed" and (
            entry.get("verdict") is None or not entry.get("resolution")
        ):
            found.append(f"{name}: a closed entry has a verdict and a resolution")
        if not entry.get("rules") or not all(RULE.match(r) for r in entry["rules"]):
            found.append(f"{name}: rules are POL- IDs")
        for text in (entry.get("question"), entry.get("resolution")):
            if text and re.search(r"[0-9]", CITED.sub("", text)):
                found.append(
                    f"{name}: a question or resolution holds a digit that isn't a citation"
                )
        for match in entry.get("matches") or [None]:
            if not isinstance(match, Mapping) or set(match) - set(WHERE):
                found.append(f"{name}: a match names only {', '.join(WHERE)}")
                continue
            if match.get("situation") not in generator.BY_NAME:
                found.append(f"{name}: unknown situation")
            if match.get("language") not in (None, "es", "pt"):
                found.append(f"{name}: unknown language")
            if match.get("check") not in CHECKS:
                found.append(f"{name}: unknown check")
            if not all(_value_ok(match.get(k)) for k in ("expected", "observed")):
                found.append(
                    f"{name}: a match's values are enums, tools, or placeholders"
                )
    return found


def covers(
    entries: Sequence[Mapping[str, Any]],
    case: Mapping[str, Any],
    finding: Mapping[str, Any],
) -> str | None:
    """
    The open entry that matches a finding on a case, if any.
    """
    for entry in entries:
        if entry["status"] != "open":
            continue
        for match in entry["matches"]:
            if (
                match["situation"] == case["situation"]
                and match.get("language", case["language"]) == case["language"]
                and all(
                    match.get(k) == finding.get(k)
                    for k in ("turn", "check", "expected", "observed")
                )
            ):
                return str(entry["id"])
    return None


def _shown(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(f"`{v}`" for v in value) or "none"
    return f"`{value}`"


def render(entries: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Disagreements between the system and the oracle",
        "",
        "Where the system and the oracle differ, we record the question here and, once triaged, whether the oracle, the "
        "system, or the policy's wording was wrong ([ADR-0005](../adr/0005-offline-scenario-evaluation.md), The oracle). "
        "CI's regression gate lets a finding through only while an open entry matches it. Generated from "
        "`disagreements.json` by `make disagreements`: edit the entries, not this page. Entries hold rule IDs and "
        "enums, never a record's value.",
        "",
        "| ID | Status | Verdict | Rules | Situations |",
        "|---|---|---|---|---|",
    ]
    for e in entries:
        situations = sorted({m["situation"] for m in e["matches"]})
        lines.append(
            f"| [{e['id']}](#{e['id'].lower()}) | {e['status'].capitalize()} | {VERDICTS[e['verdict']]} | "
            f"{', '.join(e['rules'])} | {', '.join(f'`{s}`' for s in situations)} |"
        )
    for e in entries:
        lines += ["", f"## {e['id']}", "", e["question"], ""]
        for m in e["matches"]:
            where = f"`{m['situation']}`" + (
                f" ({m['language']})" if m.get("language") else ""
            )
            turn = f", turn {m['turn']}" if m.get("turn") is not None else ""
            lines.append(
                f"- {where}{turn}: `{m['check']}`, expected {_shown(m['expected'])}, observed {_shown(m['observed'])}"
            )
        lines += ["", f"**Verdict:** {VERDICTS[e['verdict']]}."]
        if e.get("resolution"):
            lines += ["", f"**Resolution:** {e['resolution']}"]
    return "\n".join(lines) + "\n"


def write(log: Path = LOG, page: Path = PAGE) -> None:
    page.write_text(render(load(log)), encoding="utf-8")
