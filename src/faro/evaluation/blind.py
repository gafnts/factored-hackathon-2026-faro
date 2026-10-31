"""
The judge's validation sample (ADR-0005, Grading: validated before use; decision 6; EVL-10). From a run's stored
evidence it draws 30 Spanish and 30 Portuguese replies, stratified by the outcome the turn decided so the handoffs are
among them, and seeds failing replies: for each question, replies outside the natural draw that the question applies
to, each edited by one of the edits in seeds.json to break exactly that point and labeled seeded. The selection set
hasn't been graded, so how many natural replies deserve a no isn't known when the sample is drawn; each question gets
the bar's count of seeded replies, or more if asked, never fewer, and the agreement report counts natural and seeded
apart.

The sheet a person fills shows the natural and seeded replies shuffled together, under row numbers, with what the
judge reads and nothing of the key: which rows are seeded, and from which reply, stays in key.json. Only a sample drawn
from the selection set played end to end, the replies the held-out run's model writes, validates the judge; any other
is for checking the tooling, and its manifest says so. Everything stays under data/evaluation/judge/samples/, since
the replies hold the cases' values (SEC-03).
"""

import csv
import hashlib
import json
import math
import random
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import Any

from faro.evaluation import judge
from faro.evaluation.judge import Item
from faro.evaluation.rubric import Rubric

PER_LANGUAGE = 30
SEED = 20261002
OUT = Path("data/evaluation/judge/samples")
NOT_ASKED = "n/a"
LANGUAGES = ("es", "pt")
EDITS = (
    "substitute",
    "prepend",
    "append",
    "append_by_outcome",
    "remove",
    "replace_paragraph",
    "handoff_summary",
    "scramble",
)


class SampleError(ValueError):
    pass


def raw_seeds() -> bytes:
    return files(__package__).joinpath("seeds.json").read_bytes()


def load_seeds() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(raw_seeds())
    return loaded


def seed_problems(rubric: Rubric, seeds: Mapping[str, Any] | None = None) -> list[str]:
    """
    Every question has edits in both languages, each of a kind edited() applies; the tests require none found.
    """
    seeds = load_seeds() if seeds is None else seeds
    found = []
    for q in rubric.questions:
        for language in LANGUAGES:
            edits = seeds["edits"].get(q.id, {}).get(language, [])
            if not edits:
                found.append(f"{q.id} has no {language} edit")
            found += [
                f"{q.id} has an edit of unknown kind"
                for e in edits
                if e["kind"] not in EDITS
            ]
    found += [
        f"{k} isn't a question"
        for k in seeds["edits"]
        if k not in {q.id for q in rubric.questions}
    ]
    return found


def outcome(item: Item) -> str:
    """
    The outcome a reply's turn ended on, which the draw stratifies by.
    """
    return str(item.decisions[-1]["outcome"]) if item.decisions else "none"


def allocate(sizes: Mapping[str, int], total: int) -> dict[str, int]:
    """
    total split over the strata in proportion to their sizes, at least one each while total allows, the rest by
    largest remainder.
    """
    present = {k: v for k, v in sorted(sizes.items()) if v}
    whole = sum(present.values())
    if whole <= total:
        return present
    quota = {k: total * v / whole for k, v in present.items()}
    floor = 1 if total >= len(present) else 0
    given = {k: min(v, max(floor, math.floor(quota[k]))) for k, v in present.items()}
    while sum(given.values()) < total:
        k = max(
            (k for k in present if given[k] < present[k]),
            key=lambda k: (quota[k] - given[k], k),
        )
        given[k] += 1
    while sum(given.values()) > total:
        k = max(
            (k for k in given if given[k] > floor),
            key=lambda k: (given[k] - quota[k], k),
        )
        given[k] -= 1
    return given


def draw(
    found: Sequence[Item], rng: random.Random, per_language: int = PER_LANGUAGE
) -> list[Item]:
    chosen: list[Item] = []
    for language in LANGUAGES:
        spoken = [i for i in found if i.language == language]
        strata: dict[str, list[Item]] = {}
        for item in spoken:
            strata.setdefault(outcome(item), []).append(item)
        counts = allocate({k: len(v) for k, v in strata.items()}, per_language)
        for name in sorted(counts):
            chosen += rng.sample(strata[name], counts[name])
    return chosen


def paragraphs(text: str) -> list[str]:
    return [p for p in text.split("\n\n") if p.strip()]


def edited(item: Item, edit: Mapping[str, Any]) -> Item | None:
    """
    The item with the edit applied, or None when the edit leaves it as it was or empty.
    """
    kind, text = edit["kind"], item.reply
    if kind == "handoff_summary":
        if item.handoff is None:
            return None
        handoff = json.loads(json.dumps(item.handoff))
        summary = handoff["request"]["summary"]
        handoff["request"]["summary"] = (
            f"{summary} {edit['text']}" if edit["mode"] == "append" else edit["text"]
        )
        if edit.get("clear_statements"):
            handoff["customer_statements"] = []
        return replace(item, handoff=handoff) if handoff != item.handoff else None
    if kind == "substitute":
        for old, new in edit["pairs"]:
            text = text.replace(old, new)
    elif kind == "prepend":
        text = f"{edit['text']}\n\n{text}"
    elif kind == "append":
        text = f"{text}\n\n{edit['text']}"
    elif kind == "append_by_outcome":
        last = item.decisions[-1] if item.decisions else {}
        said = edit["texts"].get(
            f"{last.get('outcome')}.{last.get('awaiting')}"
        ) or edit["texts"].get(str(last.get("outcome")))
        if said is None:
            return None
        text = f"{text}\n\n{said}"
    elif kind == "remove":
        text = re.sub(edit["pattern"], "", text).strip()
    elif kind == "replace_paragraph":
        pattern = re.compile(edit["pattern"])
        text = "\n\n".join(
            edit["text"] if pattern.search(p) else p for p in paragraphs(text)
        )
    elif kind == "scramble":
        text = " ".join(
            [edit["text"], *(p.replace("\n", " ") for p in reversed(paragraphs(text)))]
        )
    if not text.strip() or text == item.reply:
        return None
    return replace(item, reply=text)


def seeded(
    pool: Sequence[Item],
    rubric: Rubric,
    seeds: Mapping[str, Any],
    rng: random.Random,
    per_question: int,
) -> tuple[list[Item], dict[str, int]]:
    """
    Up to per_question seeded replies per question, alternating the languages, each from a reply no other seed and no
    natural draw uses; returns them and how many each question fell short by.
    """
    free = list(pool)
    rng.shuffle(free)
    made: list[Item] = []
    short: dict[str, int] = {}
    for q in rubric.questions:
        count = 0
        for n in range(per_question):
            language = LANGUAGES[n % 2]
            edits = seeds["edits"][q.id][language]
            # Each language's seeds take its edits in turn.
            turn = (n // 2) % len(edits)
            hit = None
            for item in [
                i for i in free if i.language == language and q.applies(i.facts)
            ]:
                options = edits[turn:] + edits[:turn]
                hit = next(
                    (e for e in (edited(item, o) for o in options) if e is not None),
                    None,
                )
                if hit is not None:
                    free.remove(item)
                    break
            if hit is None:
                continue
            count += 1
            made.append(
                replace(
                    hit,
                    item_id=f"seeded-{q.id}-{count:02d}",
                    seeded_for=q.id,
                    source=hit.item_id,
                )
            )
        if count < per_question:
            short[q.id] = per_question - count
    return made, short


def sheet_rows(rows: Sequence[Item], rubric: Rubric) -> list[dict[str, str]]:
    found = []
    for n, item in enumerate(rows, 1):
        asked = {q.id for q in rubric.applicable(item.facts)}
        row = {
            "row": str(n),
            "earlier": "\n".join(f"Customer: {a}\nChat: {b}" for a, b in item.earlier),
            "sent": item.sent,
            "decision": "\n".join(
                f"{d['label'] or 'none'}: {d['outcome']}, waits for {d['awaiting']}, "
                f"queued {', '.join(d['queued']) or 'none'}, rules {', '.join(d['rules']) or 'none'}"
                for d in item.decisions
            ),
            "reply": item.reply,
            "handoff": json.dumps(item.handoff, ensure_ascii=False, indent=1)
            if item.handoff
            else "",
        }
        for q in rubric.questions:
            row[header(q.id, rubric)] = "" if q.id in asked else NOT_ASKED
        found.append(row)
    return found


def header(question_id: str, rubric: Rubric) -> str:
    q = rubric.question(question_id)
    if q.type == "score":
        return f"{q.id} ({q.scale[0]}-{q.scale[1]})"
    return f"{q.id} ({'|'.join(str(v) for v in q.values())})"


def guide(rubric: Rubric, rows: int) -> str:
    """
    What the person grading reads first: how to fill the sheet, then the judge's own instructions and questions, so
    both grade against the same text.
    """
    return "\n".join(
        [
            "# Blind grading sheet",
            "",
            f"Grade the {rows} rows of `sheet.csv` by hand, before seeing any judgment, and don't open `key.json`,",
            "`items.jsonl`, or a judge run's files until every row is graded. Each question's column is empty where",
            f"the row asks it and `{NOT_ASKED}` where it doesn't; its header lists the answers it takes. Write one",
            "answer per cell, exactly as listed. Leave the other columns as they are. The text below is what the",
            "judge reads before every reply.",
            "",
            "---",
            "",
            judge.system_prompt(rubric),
        ]
    )


def write_sheet(path: Path, rows: Sequence[dict[str, str]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as kept:
        writer = csv.DictWriter(kept, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_sheet(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as kept:
        return list(csv.DictReader(kept))


def run_info(run: Path) -> dict[str, Any]:
    """
    Which set a run played and how, from the summary an in-process play keeps or the manifest an end-to-end run does.
    """
    manifest = run / "manifest.json"
    if manifest.is_file():
        body = json.loads(manifest.read_text(encoding="utf-8"))
        return {"run": run.name, "set": body["set"]["name"], "mode": body["mode"]}
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    return {"run": run.name, "set": summary["set"], "mode": summary["mode"]}


def sample(
    run: Path,
    out: Path,
    rubric: Rubric,
    seed: int = SEED,
    per_language: int = PER_LANGUAGE,
    per_question: int | None = None,
) -> dict[str, Any]:
    """
    Draws, seeds, and writes the sheet, its guide, the key, the items the judge reads, and a manifest; returns it.
    """
    floor = rubric.bar["deserve_no"]
    count = floor if per_question is None else per_question
    if count < floor:
        raise SampleError(
            f"each question takes at least the bar's {floor} seeded replies, not {count}"
        )
    info = run_info(run)
    found = judge.run_items(run)
    if not found:
        raise SampleError("the run holds no reply to draw from")
    rng = random.Random(seed)
    natural = draw(found, rng, per_language)
    taken = {i.item_id for i in natural}
    seeds = load_seeds()
    made, short = seeded(
        [i for i in found if i.item_id not in taken], rubric, seeds, rng, count
    )
    rows = natural + made
    rng.shuffle(rows)
    hashes = {
        "items.jsonl": judge.write_items(out / "items.jsonl", rows),
        "sheet.csv": write_sheet(out / "sheet.csv", sheet_rows(rows, rubric)),
    }
    (out / "guide.md").write_text(guide(rubric, len(rows)) + "\n", encoding="utf-8")
    key = [
        {
            "row": n,
            "item_id": i.item_id,
            "seeded_for": i.seeded_for,
            "source": i.source,
            "language": i.language,
        }
        for n, i in enumerate(rows, 1)
    ]
    (out / "key.json").write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")
    manifest = {
        "sample": out.name,
        "source": info,
        "validates": info["set"] == "selection" and info["mode"] == "end_to_end",
        "seed": seed,
        "rubric": {"version": rubric.version, "sha256": rubric.sha256},
        "seeds": {
            "version": seeds["version"],
            "sha256": hashlib.sha256(raw_seeds()).hexdigest(),
        },
        "natural": dict(sorted(Counter(i.language for i in natural).items())),
        "natural_by_outcome": dict(
            sorted(Counter(outcome(i) for i in natural).items())
        ),
        "seeded": dict(sorted(Counter(str(i.seeded_for) for i in made).items())),
        "seeded_short": short,
        "rows": len(rows),
        "results": hashes,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
