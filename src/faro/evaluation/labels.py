"""
Label quality (DML-08, EVL-08; ADR-0005, Grading, Labels): 50 of the families' messages relabeled by hand, blind to
the labels their families carry, and the agreement reported. relabel-sample draws the sheet, stratified over the
request groups and alternating the languages, from every family on both sides of the split, since the labels under
test are the families' own; the person reads the policy's request list first and writes every request the message
makes, or none. relabel-agreement scores the filled sheet: exact agreement on the set of labels with a Wilson
interval, Cohen's kappa on the first label with a bootstrap interval, and the disagreements by row for the policy's
text to settle, a message dropped when it can't (ADR-0005). The files hold message IDs and labels, never a text but
the sheet's own (SEC-03).
"""

import csv
import hashlib
import json
import random
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from faro.evaluation import agreement, blind, families, intervals
from faro.evaluation.families import Family, Message

OUT = Path("data/evaluation/labels")
SEED = 20261003
ROWS = 50
LANGUAGES = ("es", "pt")
NONE = "none"
# The policy's request labels in POL-05's order, then none for a message with no request (POL-06).
LABELS = (
    "block_card",
    "unrecognized_charge",
    "talk_to_human",
    "decline_reason",
    "card_status",
    "available_credit",
    "recent_transactions",
    "unsupported",
)
REQUESTS = {
    "card_status": "Status of one of my cards (POL-01, POL-21)",
    "available_credit": "Credit left on a credit card (POL-22)",
    "recent_transactions": "My recent card transactions (POL-25)",
    "decline_reason": "Why was my card declined (POL-02, POL-27 to POL-29)",
    "block_card": "Block my card, including lost or stolen, with a reason (POL-03, POL-33 to POL-38)",
    "unrecognized_charge": "I don't recognize this charge (POL-39)",
    "unsupported": "Unblocking, replacements, PIN changes, limit increases, accounts, loans, anything else the chat doesn't serve (POL-41 to POL-43)",
    "talk_to_human": "Complaints; asking for a person (POL-44)",
}


class LabelsError(ValueError):
    pass


@dataclass(frozen=True)
class Drawn:
    message: Message
    family: Family
    side: str


def ordered(labels: Sequence[str]) -> tuple[str, ...]:
    """
    A label set in POL-05's order, none standing alone for a message with no request.
    """
    found = tuple(label for label in LABELS if label in labels)
    return found or (NONE,)


def parse(cell: str) -> tuple[str, ...]:
    """
    The labels a cell holds, separated by ; or , in any order and case; unknown words are an error, not a label.
    """
    words = [w.strip().lower() for w in cell.replace(",", ";").split(";") if w.strip()]
    unknown = [w for w in words if w not in (*LABELS, NONE)]
    if unknown:
        raise LabelsError(
            f"unknown label {', '.join(unknown)}; the labels are {', '.join((*LABELS, NONE))}"
        )
    if not words:
        raise LabelsError("an empty cell; write none for a message with no request")
    return ordered([w for w in words if w != NONE])


def pool(loaded: Sequence[Family], held: Collection[str]) -> list[Drawn]:
    return [
        Drawn(m, f, "held_out" if f.family_id in held else "development")
        for f in loaded
        for m in f.messages
        if m.language in LANGUAGES
    ]


def draw(found: Sequence[Drawn], rng: random.Random, rows: int = ROWS) -> list[Drawn]:
    """
    rows messages across the request groups in proportion, each group alternating the languages and spreading over
    its families: a family gives a second message only once every family of the group gave one.
    """
    by_group: dict[str, list[Drawn]] = {}
    for d in found:
        by_group.setdefault(d.family.group, []).append(d)
    shares = blind.allocate({g: len(ms) for g, ms in by_group.items()}, rows)
    chosen: list[Drawn] = []
    for group in sorted(shares):
        wanted = shares[group]
        members = list(by_group[group])
        rng.shuffle(members)
        taken: Counter[str] = Counter()
        picked: list[Drawn] = []
        for n in range(wanted):
            language = LANGUAGES[(len(chosen) + n) % 2]
            fewest = min(
                (
                    taken[d.family.family_id]
                    for d in members
                    if d.message.language == language
                ),
                default=None,
            )
            if fewest is None:
                continue
            pick = next(
                d
                for d in members
                if d.message.language == language
                and taken[d.family.family_id] == fewest
            )
            members.remove(pick)
            taken[pick.family.family_id] += 1
            picked.append(pick)
        chosen += picked
    rng.shuffle(chosen)
    return chosen


def guide(rows: int) -> str:
    lines = [
        "# Relabel sheet",
        "",
        f"Label the {rows} rows of `sheet.csv` by hand, blind: don't open `key.json` or any family file until every",
        "row is labeled. Each row is one message a customer could send the chat, with nothing before it. In the",
        "`labels` column write every request the message makes, from the list below, separated by `;`, or `none`",
        "for a message that makes no request the chat serves (a greeting, thanks, a question about what the chat",
        "can do). The order doesn't matter. Leave the other columns as they are.",
        "",
        "The policy's request list (card-support.md, Requests). A label names what the customer asks, never an",
        "outcome the policy decides after reading state (POL-04). A message that asks for something the chat",
        "doesn't serve, an unblock, a replacement, a PIN, a limit, an account, a loan, is `unsupported`, not none.",
        "A message that asks for a person, or complains, is `talk_to_human`. A charge the customer doesn't recognize",
        "is `unrecognized_charge` even when they also ask to block the card for it; write both only when the",
        "message asks for both as separate requests.",
        "",
        "| Label | Request |",
        "|---|---|",
    ]
    for label in LABELS:
        lines.append(f"| `{label}` | {REQUESTS[label]} |")
    lines.append(f"| `{NONE}` | No request the chat serves (POL-06) |")
    return "\n".join(lines)


def sheet_rows(chosen: Sequence[Drawn]) -> list[dict[str, str]]:
    return [
        {
            "row": str(n),
            "language": d.message.language,
            "message": d.message.text,
            "labels": "",
        }
        for n, d in enumerate(chosen, 1)
    ]


def sample(
    out: Path,
    loaded: Sequence[Family] | None = None,
    held: Collection[str] | None = None,
    seed: int = SEED,
    rows: int = ROWS,
) -> dict[str, Any]:
    """
    Draws and writes the sheet, its guide, the key, and a manifest; returns the manifest.
    """
    loaded = families.load() if loaded is None else loaded
    if held is None:
        held = families.held_out_ids(loaded, families.load_answers())
    found = pool(loaded, held)
    if not found:
        raise LabelsError("no message to draw from")
    chosen = draw(found, random.Random(seed), rows)
    out.mkdir(parents=True, exist_ok=True)
    digest = blind.write_sheet(out / "sheet.csv", sheet_rows(chosen))
    (out / "guide.md").write_text(guide(len(chosen)) + "\n", encoding="utf-8")
    key = [
        {
            "row": n,
            "message_id": d.message.id,
            "family_id": d.family.family_id,
            "group": d.family.group,
            "kind": d.family.kind,
            "side": d.side,
            "author": d.message.author,
            "clear": d.message.clear,
            "labels": list(ordered(d.family.labels)),
        }
        for n, d in enumerate(chosen, 1)
    ]
    (out / "key.json").write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")
    manifest = {
        "sample": out.name,
        "seed": seed,
        "rows": len(chosen),
        "families": {"sha256": _families_hash(), "count": len(loaded)},
        "by_language": dict(
            sorted(Counter(d.message.language for d in chosen).items())
        ),
        "by_group": dict(sorted(Counter(d.family.group for d in chosen).items())),
        "by_side": dict(sorted(Counter(d.side for d in chosen).items())),
        "by_author": dict(sorted(Counter(d.message.author for d in chosen).items())),
        "results": {"sheet.csv": digest},
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _families_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(families.__file__).parent.glob("*.json")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def score(sample_dir: Path) -> dict[str, Any]:
    """
    The filled sheet against the key: exact agreement on the label set, kappa on the first label, and every
    disagreement by row with both label sets and the family's kind, for the policy's text to settle.
    """
    manifest = json.loads((sample_dir / "manifest.json").read_text(encoding="utf-8"))
    key = {
        k["row"]: k
        for k in json.loads((sample_dir / "key.json").read_text(encoding="utf-8"))
    }
    with (sample_dir / "sheet.csv").open(encoding="utf-8", newline="") as kept:
        rows = list(csv.DictReader(kept))
    pairs: list[tuple[tuple[str, ...], tuple[str, ...], Mapping[str, Any]]] = []
    for row in rows:
        entry = key[int(row["row"])]
        try:
            hand = parse(row["labels"])
        except LabelsError as error:
            raise LabelsError(f"row {row['row']}: {error}") from error
        pairs.append((hand, tuple(entry["labels"]), entry))
    n = len(pairs)
    exact = sum(hand == theirs for hand, theirs, _ in pairs)
    bounds = intervals.wilson(exact, n)
    primary = [(hand[0], theirs[0]) for hand, theirs, _ in pairs]
    kappa = intervals.bootstrap(
        primary, lambda drawn: {"kappa": agreement.cohen(drawn)}
    )["kappa"]

    def by(field: str) -> dict[str, dict[str, int]]:
        found: dict[str, dict[str, int]] = {}
        for hand, theirs, entry in pairs:
            cell = found.setdefault(str(entry[field]), {"rows": 0, "agreed": 0})
            cell["rows"] += 1
            cell["agreed"] += hand == theirs
        return dict(sorted(found.items()))

    return {
        "sample": manifest["sample"],
        "rows": n,
        "exact": {
            "agreed": exact,
            "value": round(exact / n, 4) if n else None,
            "low": None if bounds is None else round(bounds[0], 4),
            "high": None if bounds is None else round(bounds[1], 4),
        },
        "kappa_first_label": kappa.to_json(),
        "by_group": by("group"),
        "by_kind": by("kind"),
        "by_author": by("author"),
        "by_side": by("side"),
        "disagreements": [
            {
                "row": entry["row"],
                "message_id": entry["message_id"],
                "kind": entry["kind"],
                "family": list(theirs),
                "hand": list(hand),
                "settled": None,
            }
            for hand, theirs, entry in pairs
            if hand != theirs
        ],
    }


def write(found: Mapping[str, Any], sample_dir: Path) -> Path:
    out = sample_dir / "agreement.json"
    out.write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
    return out
