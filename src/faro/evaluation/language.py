"""
The live language check (ADR-0005, The development regression set, as amended on 2026-10-02): the real prompts read
every paraphrase and answer on the development side, never the held-out side, and say which language each is mostly
in, which is compared with the label we expect (POL-50, POL-51): the family's language, unclear for a paraphrase the
family marks so, other for a third language. The scripted models answer the language from the families, so this is
where the model's own reading is measured. The result keeps counts and message IDs, never a text, and the page under
docs/evaluation/ sets it beside the word-list detector's baseline.

The check also sends each development block request through the extraction, with the context the agent sends, and
compares the block reason read with the family's (none when the family names none), and each reason answer's with its
kind (POL-35). The scripted models answer the reason from the families, so this is the only place a reason the model
reads into a bare request shows before a deployed run (D-006). Those counts stay in a block of their own, so the
language counts compare with earlier reports. So do the cards read for each answer that names one by its place in a list
("la segunda"), sent as the answer to the which-card question and as a later message after the chat listed the cards,
which no case plays (POL-13).
"""

import asyncio
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from faro.agent.models import (
    MODEL,
    ModelFailedError,
    Models,
    prompt_version,
)
from faro.agent.nodes.resolve import ASKS, EARLIER
from faro.evaluation.families import Answer, Family

PAGE = Path("docs/evaluation/language.md")
REPORTS = Path("docs/evaluation/language")
PROMPTS = ("route", "resolve_card", "find_transaction")
# Made-up values for the slots, so no message reaches the model with a record's value (SEC-03).
VALUES = {
    "last_four": "4821",
    "merchant": "Tienda Ejemplo",
    "amount": "189.90 USD",
    "date": "14/06/2026",
    "other_customer_id": "CLI-0000000000",
    "card_type": "crédito",
}
CARDS = (
    "The customer is answering which of these cards they mean:\n"
    "- credit card ending in 4821\n- debit card ending in 1177"
)
LATER = f"{ASKS['card_status']}\n{EARLIER}\n- credit card ending in 4821\n- debit card ending in 1177"
# The card each answer by place names in both lists: the second, and the last.
PLACED = "1177"
REASON = "The customer is answering why they want to block the card."
LISTING = (
    "The customer reports a charge they don't recognize.\nToday is Wednesday 2026-06-17.\n"
    "1. Sunday 2026-06-14 21:07:33, Purchase, 189.9 USD, Tienda Ejemplo, Approved, Colombia\n"
    "2. Friday 2026-06-12 12:00:00, Purchase, 25.0 USD, Mercado Central, Approved, Colombia\n"
    "The customer is answering which of these transactions they mean."
)
# Which call reads each kind of answer in the graph: a typed yes goes through the router, a card or a reason through
# the extraction, a transaction through the choice.
CALLS = {
    "card_last_four": ("extract", CARDS),
    "card_type": ("extract", CARDS),
    "card_both": ("extract", CARDS),
    "card_position": ("extract", CARDS),
    "reason_lost": ("extract", REASON),
    "reason_stolen": ("extract", REASON),
    "reason_unrecognized_charge": ("extract", REASON),
    "reason_customer_request": ("extract", REASON),
    "transaction_merchant": ("choose", LISTING),
    "transaction_amount": ("choose", LISTING),
    "transaction_date": ("choose", LISTING),
    "transaction_newest": ("choose", LISTING),
    "dont_know": ("extract", CARDS),
    "aside": ("extract", CARDS),
    "typed_yes": ("route", None),
}


@dataclass(frozen=True)
class Item:
    """
    expected is the language the item should read as, or None for an item that only reads a block reason or a card;
    reason is POL-35's code the extraction should give, none for a request that gives no reason, or None when not
    compared; card is the last four digits the extraction should give for an answer by place, or None when not compared.
    """

    id: str
    expected: str | None
    text: str
    call: str
    context: str | None = None
    reason: str | None = None
    card: str | None = None


@dataclass(frozen=True)
class Result:
    item: Item
    said: str
    reason: str | None = None
    card: str | None = None


def expected_reason(kind: str) -> str | None:
    return kind.removeprefix("reason_") if kind.startswith("reason_") else None


def filled(text: str) -> str:
    for name, value in VALUES.items():
        text = text.replace(f"{{{name}}}", value)
    return text


def expected_language(family: Family, message_id: str) -> str:
    if family.kind == "third_language":
        return "other"
    message = next(m for m in family.messages if m.id == message_id)
    return message.language if message.clear else "unclear"


def development_items(
    families: Sequence[Family],
    answers: Sequence[Answer],
    held: frozenset[str],
    only: Iterable[str] = (),
) -> list[Item]:
    """
    only narrows the check to the families and answers named, for a look at a prompt change before a whole run.
    """
    wanted = set(only)
    items = []
    for family in families:
        if family.family_id in held or (wanted and family.family_id not in wanted):
            continue
        for message in family.messages:
            items.append(
                Item(
                    message.id,
                    expected_language(family, message.id),
                    filled(message.text),
                    "route",
                )
            )
            if "block_card" in family.labels:
                items.append(
                    Item(
                        message.id,
                        None,
                        filled(message.text),
                        "extract",
                        ASKS["block_card"],
                        family.extract.get("block_reason") or "none",
                    )
                )
    for answer in answers:
        if answer.answer_id in held or (wanted and answer.answer_id not in wanted):
            continue
        call, context = CALLS[answer.kind]
        placed = PLACED if answer.kind == "card_position" else None
        for language, text in answer.texts.items():
            items.append(
                Item(
                    f"{answer.answer_id}/{language}",
                    language,
                    filled(text),
                    call,
                    context,
                    expected_reason(answer.kind),
                    placed,
                )
            )
            if placed:
                # As a later message too, which only the card's count reads.
                items.append(
                    Item(
                        f"{answer.answer_id}/{language}/later",
                        None,
                        filled(text),
                        "extract",
                        LATER,
                        card=placed,
                    )
                )
    return items


async def ask(models: Models, item: Item) -> Result:
    said: str
    reason: str | None = None
    card: str | None = None
    try:
        if item.call == "route":
            said = (await models.route(item.text)).language
        elif item.call == "extract":
            assert item.context is not None
            found = await models.extract(item.text, item.context)
            said = found.language
            details = found.details()
            reason = details["block_reason"] or "none"
            card = details["last_four"] or details["card_type"] or "none"
        else:
            assert item.context is not None
            said = (await models.choose(item.text, item.context)).language
    except ModelFailedError:
        said = reason = card = "failed"
    return Result(
        item,
        said,
        reason if item.reason is not None else None,
        card if item.card is not None else None,
    )


async def check(
    models: Models, items: Iterable[Item], parallel: int = 4
) -> list[Result]:
    gate = asyncio.Semaphore(parallel)

    async def one(item: Item) -> Result:
        async with gate:
            return await ask(models, item)

    return list(await asyncio.gather(*(one(item) for item in items)))


def counted(results: Sequence[Result], key: str) -> dict[str, dict[str, int]]:
    """
    Items and items read as expected, by the item's expected language or by its call.
    """
    totals: Counter[str] = Counter()
    read: Counter[str] = Counter()
    for result in results:
        if result.item.expected is None:
            continue
        group = result.item.expected if key == "language" else result.item.call
        totals[group] += 1
        read[group] += result.said == result.item.expected
    return {g: {"items": totals[g], "read": read[g]} for g in sorted(totals)}


def reasons(results: Sequence[Result]) -> dict[str, Any]:
    """
    The block reasons read against the family's or the answer kind's, by the reason expected.
    """
    totals: Counter[str] = Counter()
    read: Counter[str] = Counter()
    misses = []
    for result in results:
        expected = result.item.reason
        if expected is None:
            continue
        totals[expected] += 1
        if result.reason == expected:
            read[expected] += 1
        else:
            misses.append(
                {"id": result.item.id, "expected": expected, "said": result.reason}
            )
    return {
        "items": sum(totals.values()),
        "read": sum(read.values()),
        "by_reason": {g: {"items": totals[g], "read": read[g]} for g in sorted(totals)},
        "misses": misses,
    }


def places(results: Sequence[Result]) -> dict[str, Any]:
    """
    The cards read for the answers that name one by its place, as the answer to the question and as a later message,
    against the card at that place.
    """
    totals: Counter[str] = Counter()
    read: Counter[str] = Counter()
    misses = []
    for result in results:
        expected = result.item.card
        if expected is None:
            continue
        group = "later" if result.item.id.endswith("/later") else "answer"
        totals[group] += 1
        if result.card == expected:
            read[group] += 1
        else:
            misses.append(
                {"id": result.item.id, "expected": expected, "said": result.card}
            )
    return {
        "items": sum(totals.values()),
        "read": sum(read.values()),
        "by_turn": {g: {"items": totals[g], "read": read[g]} for g in sorted(totals)},
        "misses": misses,
    }


def report(
    results: Sequence[Result], cost_usd: float, now: datetime | None = None
) -> dict[str, Any]:
    """
    What the committed file holds: counts and message IDs, never a text.
    """
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    languages = [r for r in results if r.item.expected is not None]
    return {
        "check": "live",
        "date": stamp,
        "side": "development",
        "model": MODEL,
        "prompts": {name: prompt_version(name) for name in PROMPTS},
        "items": len(languages),
        "by_language": counted(results, "language"),
        "by_call": counted(results, "call"),
        "misses": [
            {"id": r.item.id, "expected": r.item.expected, "said": r.said}
            for r in languages
            if r.said != r.item.expected
        ],
        "block_reasons": reasons(results),
        "cards_by_place": places(results),
        "cost_usd": round(cost_usd, 6),
    }


def share(counts: Mapping[str, int] | None) -> str:
    if not counts or not counts["items"]:
        return ""
    return f"{counts['read']} of {counts['items']} ({100 * counts['read'] / counts['items']:.1f}%)"


def page(reports: Path = REPORTS) -> str:
    """
    The page beside the run index: the latest live check against the baseline, per language and per call, with the
    live check's misses by message ID.
    """
    found = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(reports.glob("*.json"))
    ]
    baseline = next((r for r in found if r["check"] == "baseline"), None)
    live = sorted((r for r in found if r["check"] == "live"), key=lambda r: r["date"])
    latest = live[-1] if live else None
    lines = [
        "# Language check",
        "",
        "Generated by `make language-check` from the files under `language/`; do not edit. The real prompts read every",
        "paraphrase and answer on the development side, never the held-out side, and say which language each is mostly",
        "in. The label we expect is the family's language, `unclear` for a paraphrase the family marks as not clearly in",
        "one language, and `other` for a third language (POL-50, POL-51; [ADR-0005](../adr/0005-offline-scenario-evaluation.md),",
        "The development regression set). The baseline is the word-list detector the agent used until 2026-10-02, read",
        "from the other language's conversation, so a message had to set its own language to count. Claude Opus 5.5, an",
        "Anthropic model, wrote the paraphrases, and an Anthropic model reads them here, so the result may flatter it",
        "([ADR-0005](../adr/0005-offline-scenario-evaluation.md), The split). The check also sends each development",
        "block request through the extraction and compares the block reason read with the family's, in the last",
        "section; those items stay out of the language tables, so the tables compare across reports (POL-35, D-006).",
        "Each answer that names a card by its place in a list is also sent as a later message, after the chat listed the",
        "cards, and the card read is counted in a section of its own (POL-13).",
        "",
    ]
    if latest is not None:
        lines += [
            f"Latest live check: {latest['date']}, {latest['model']}, {latest['items']} items, "
            f"{latest['cost_usd']:.4f} USD at list price. Prompts: "
            + ", ".join(f"`{k}` {v}" for k, v in latest["prompts"].items())
            + ".",
            "",
        ]
    languages = sorted(
        {
            *(baseline or {}).get("by_language", {}),
            *(latest or {}).get("by_language", {}),
        }
    )
    lines += ["| Expected | Baseline read | Live read |", "|---|---|---|"]
    for language in languages:
        lines.append(
            f"| {language} | {share((baseline or {}).get('by_language', {}).get(language))} "
            f"| {share((latest or {}).get('by_language', {}).get(language))} |"
        )
    if latest is not None:
        lines += ["", "| Call | Live read |", "|---|---|"]
        for call, counts in latest["by_call"].items():
            lines.append(f"| {call} | {share(counts)} |")
        lines += ["", "## Misses, latest live check", ""]
        if latest["misses"]:
            lines += ["| Message | Expected | Said |", "|---|---|---|"]
            lines += [
                f"| {m['id']} | {m['expected']} | {m['said']} |"
                for m in latest["misses"]
            ]
        else:
            lines.append("None.")
        if "block_reasons" in latest:
            lines += reason_lines(latest["block_reasons"])
        if "cards_by_place" in latest:
            lines += place_lines(latest["cards_by_place"])
    return "\n".join(lines) + "\n"


def reason_lines(found: Mapping[str, Any]) -> list[str]:
    lines = [
        "",
        "## Block reasons, latest live check",
        "",
        f"{share(found)} of the block requests and reason answers read with the reason we expect: the family's, `none`",
        "when the request gives no reason, so POL-35's question should follow, or the reason answer's kind.",
        "",
        "| Expected | Live read |",
        "|---|---|",
    ]
    lines += [
        f"| {reason} | {share(counts)} |"
        for reason, counts in found["by_reason"].items()
    ]
    lines += ["", "### Misses", ""]
    if found["misses"]:
        lines += ["| Message | Expected | Said |", "|---|---|---|"]
        lines += [
            f"| {m['id']} | {m['expected']} | {m['said']} |" for m in found["misses"]
        ]
    else:
        lines.append("None.")
    return lines


def place_lines(found: Mapping[str, Any]) -> list[str]:
    lines = [
        "",
        "## Cards named by their place, latest live check",
        "",
        f'{share(found)} of the answers that name a card by its place in a list ("la segunda") read as the card at',
        "that place: as the answer to the which-card question, and as a later message after the chat listed the cards.",
        "",
        "| Read as | Live read |",
        "|---|---|",
    ]
    lines += [
        f"| {turn} | {share(counts)} |" for turn, counts in found["by_turn"].items()
    ]
    lines += ["", "### Misses", ""]
    if found["misses"]:
        lines += ["| Message | Expected | Said |", "|---|---|---|"]
        lines += [
            f"| {m['id']} | {m['expected']} | {m['said']} |" for m in found["misses"]
        ]
    else:
        lines.append("None.")
    return lines


def write(
    found: Mapping[str, Any], reports: Path = REPORTS, page_path: Path = PAGE
) -> Path:
    reports.mkdir(parents=True, exist_ok=True)
    out = reports / f"{found['date'][:10]}-live.json"
    if out.exists():
        # A second run on one day keeps the first; the page reads the latest by its date.
        out = reports / f"{found['date'][:16].replace(':', '-')}-live.json"
    out.write_text(
        json.dumps(found, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    page_path.write_text(page(reports), encoding="utf-8")
    return out
