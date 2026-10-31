"""
The judge's agreement with blind hand grades (ADR-0005, Grading: validated before use; EVL-10). It reads a filled
sheet with its key, and a judge run over the same items, and gives per question: agreement on the verdict an answer
implies, with a Wilson interval; Cohen's kappa with a percentile bootstrap interval over the graded replies, on the
verdict for a yes or no, on the categories for a choice, and with linear weights on the scale for a score; how many replies deserve a no by hand, natural and seeded apart; and the share of those the judge caught. A
seeded reply counts only for the question it was seeded for. A question is graded by the judge only if its agreement
and kappa reach the bar and enough replies deserve a no; otherwise by hand, or, for the one question the rubric lets
go, by hand or dropped. The report holds counts and figures only, never a reply.
"""

import json
from collections import Counter
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from faro.evaluation import blind, intervals, judge
from faro.evaluation.rubric import Question, Rubric


class AgreementError(ValueError):
    pass


@dataclass(frozen=True)
class Pair:
    """
    One graded reply for one question: the hand's answer and the judge's, the verdicts they imply, and whether the
    reply was seeded for this question.
    """

    hand: Any
    judged: Any
    hand_passes: bool
    judge_passes: bool
    seeded: bool
    unsure: bool


def cohen(pairs: Sequence[tuple[Hashable, Hashable]]) -> float | None:
    """
    Undefined when chance agreement is total, as when both graders gave one answer throughout.
    """
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(a == b for a, b in pairs) / n
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    chance = sum(first[c] * second[c] for c in set(first) | set(second)) / (n * n)
    if chance == 1:
        return None
    return (observed - chance) / (1 - chance)


def weighted(pairs: Sequence[tuple[int, int]], scale: tuple[int, int]) -> float | None:
    """
    Kappa on an ordinal scale with linear disagreement weights, |i - j| over the scale's span. The weights are part of
    the bar, fixed before any grade (ADR-0005, Grading): chosen after seeing the grades, they would move it.
    """
    n, span = len(pairs), scale[1] - scale[0]
    if n == 0 or span == 0:
        return None

    def weight(i: int, j: int) -> float:
        return abs(i - j) / span

    observed = sum(weight(a, b) for a, b in pairs) / n
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(
        first[i] * second[j] * weight(i, j) for i in first for j in second
    ) / (n * n)
    if expected == 0:
        return None
    return 1 - observed / expected


def kappa(q: Question, pairs: Sequence[Pair]) -> float | None:
    if q.type == "score":
        return weighted([(p.hand, p.judged) for p in pairs], q.scale)
    if q.type == "choice":
        return cohen([(p.hand, p.judged) for p in pairs])
    return cohen([(p.hand_passes, p.judge_passes) for p in pairs])


def parsed(q: Question, cell: str) -> Any:
    value = cell.strip()
    if q.type == "score":
        return int(value) if value.lstrip("-").isdigit() else None
    if q.type == "yes_no":
        value = value.lower()
    return value if value in q.values() else None


def pairs_for(
    q: Question,
    rows: Sequence[Mapping[str, str]],
    key: Mapping[str, Mapping[str, Any]],
    items: Mapping[str, judge.Item],
    judged: Mapping[str, Mapping[str, Any]],
    rubric: Rubric,
) -> tuple[list[Pair], int]:
    """
    The question's graded pairs, and how many replies the judge left unanswered for it.
    """
    column = blind.header(q.id, rubric)
    found: list[Pair] = []
    unanswered = 0
    for row in rows:
        cell = row[column].strip()
        if cell == blind.NOT_ASKED:
            continue
        entry = key[row["row"]]
        if entry["seeded_for"] not in (None, q.id):
            continue
        hand = parsed(q, cell)
        if hand is None:
            raise AgreementError(f"row {row['row']} has no valid {q.id} answer")
        given = (judged.get(entry["item_id"]) or {}).get("questions", {}).get(
            q.id
        ) or {}
        if given.get("answer") is None:
            unanswered += 1
            continue
        item = items[entry["item_id"]]
        found.append(
            Pair(
                hand=hand,
                judged=given["answer"],
                hand_passes=q.passes(hand, item.language),
                judge_passes=q.passes(given["answer"], item.language),
                seeded=entry["seeded_for"] == q.id,
                unsure=bool(given.get("by_hand")),
            )
        )
    return found, unanswered


def caught(pairs: Sequence[Pair]) -> dict[str, int]:
    failing = [p for p in pairs if not p.hand_passes]
    return {"failing": len(failing), "caught": sum(not p.judge_passes for p in failing)}


def score(
    q: Question, pairs: Sequence[Pair], unanswered: int, rubric: Rubric
) -> dict[str, Any]:
    bar = rubric.bar
    agreed = sum(p.hand_passes == p.judge_passes for p in pairs)
    share = agreed / len(pairs) if pairs else None
    bounds = intervals.wilson(agreed, len(pairs))
    estimates = intervals.bootstrap(pairs, lambda drawn: {"kappa": kappa(q, drawn)})
    deserve = {
        "natural": sum(not p.hand_passes for p in pairs if not p.seeded),
        "seeded": sum(not p.hand_passes for p in pairs if p.seeded),
    }
    found_kappa = estimates["kappa"].value
    why = []
    if share is None or share < bar["agreement"]:
        why.append("agreement below the bar")
    if found_kappa is None or found_kappa < bar["kappa"]:
        why.append("kappa below the bar or undefined")
    if sum(deserve.values()) < bar["deserve_no"]:
        why.append("too few replies deserve a no")
    verdict = "judge" if not why else ("hand_or_drop" if q.droppable else "hand")
    return {
        "question": q.id,
        "type": q.type,
        "rules": list(q.rules),
        "pairs": len(pairs),
        "natural": sum(not p.seeded for p in pairs),
        "seeded": sum(p.seeded for p in pairs),
        "unanswered": unanswered,
        "low_confidence": sum(p.unsure for p in pairs),
        "deserve_no": deserve,
        "agreement": {
            "value": None if share is None else round(share, 4),
            "low": None if bounds is None else round(bounds[0], 4),
            "high": None if bounds is None else round(bounds[1], 4),
        },
        "exact": round(sum(p.hand == p.judged for p in pairs) / len(pairs), 4)
        if pairs
        else None,
        "kappa": {
            **estimates["kappa"].to_json(),
            "kind": "weighted_linear" if q.type == "score" else "cohen",
        },
        "caught": {
            "natural": caught([p for p in pairs if not p.seeded]),
            "seeded": caught([p for p in pairs if p.seeded]),
        },
        "verdict": verdict,
        "why": why,
    }


def report(sample: Path, judged_run: Path, rubric: Rubric) -> dict[str, Any]:
    made = json.loads((sample / "manifest.json").read_text(encoding="utf-8"))
    ran = json.loads((judged_run / "manifest.json").read_text(encoding="utf-8"))
    if ran["results"]["items.jsonl"] != made["results"]["items.jsonl"]:
        raise AgreementError("the judge run didn't judge this sample's items")
    if (
        made["rubric"]["sha256"] != rubric.sha256
        or ran["judge"]["rubric"]["sha256"] != rubric.sha256
    ):
        raise AgreementError("the sample or the judge run used another rubric")
    rows = blind.read_sheet(sample / "sheet.csv")
    key = {
        str(k["row"]): k
        for k in json.loads((sample / "key.json").read_text(encoding="utf-8"))
    }
    items = {i.item_id: i for i in judge.read_items(sample / "items.jsonl")}
    with (judged_run / "judgments.jsonl").open(encoding="utf-8") as kept:
        judged = {j["item_id"]: j for j in map(json.loads, kept)}
    questions = []
    for q in rubric.questions:
        pairs, unanswered = pairs_for(q, rows, key, items, judged, rubric)
        questions.append(score(q, pairs, unanswered, rubric))
    return {
        "sample": made["sample"],
        "source": made["source"],
        "validates": made["validates"],
        "judge_run": ran["run"],
        "judge": {
            k: ran["judge"][k]
            for k in (
                "model_requested",
                "model_returned",
                "prompt_version",
                "rubric",
                "settings",
            )
        },
        "bar": dict(rubric.bar),
        "bootstrap": {
            "resamples": intervals.RESAMPLES,
            "seed": intervals.SEED,
            "level": intervals.LEVEL,
        },
        "questions": questions,
    }


def write(found: Mapping[str, Any], sample: Path) -> Path:
    out = sample / f"agreement-{found['judge_run']}.json"
    out.write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
    return out
