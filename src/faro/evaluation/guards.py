"""
The split's guards (ADR-0005, The split): a case's customer and answers sit on its side, its messages on the side its
phrasing names (a held-out situation no held-out family fits borrows development phrasing), and no development
artifact (cases, prompts, the baseline's keywords, the router's data) holds a held-out customer or a held-out family's
words. The tests run them over every artifact in the repository; the generator runs them over every set it writes.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from faro.evaluation.families import TOO_CLOSE, normalized, trigrams
from faro.split import held_out

# A shorter held-out message inside a longer text can be chance ("mi tarjeta"); the similarity check still catches it
# as a line of its own.
CONTAINED_WORDS = 3


def case_problems(case: Mapping[str, Any], held_families: frozenset[str]) -> list[str]:
    """
    held_families: the held-out families' and answers' IDs (families.held_out_ids).
    """
    want = case["side"] == "held_out"
    phrasing = case.get("phrasing", case["side"])
    found = []
    if phrasing != case["side"] and not want:
        found.append("a development case borrows no phrasing")
    customers = [("customer", case["customer_id"])]
    if "other_customer_id" in case["script"]["means"]:
        customers.append(
            ("other customer", case["script"]["means"]["other_customer_id"])
        )
    for name, customer_id in customers:
        if held_out(customer_id) != want:
            found.append(f"its {name} isn't on the {case['side']} side")
    sides = {
        a["id"].split("/")[0]: case["side"]
        for a in case["script"]["answers"].values()
        if isinstance(a, dict)
    }
    sides |= {m["id"].split("/")[0]: phrasing for m in case["script"]["messages"]}
    if case["family_id"] is not None:
        sides[case["family_id"]] = phrasing
    for authored, side in sorted(sides.items()):
        if (authored in held_families) != (side == "held_out"):
            found.append(f"{authored} isn't on the {side} side")
    return found


def leaks(
    artifacts: Mapping[str, str], held_messages: Iterable[tuple[str, str]]
) -> list[str]:
    """
    artifacts: a development artifact's text by its name; held_messages: (ID, text) of every held-out message.
    """
    lines = {
        (name, n): trigrams(line)
        for name, text in artifacts.items()
        for n, line in enumerate(text.splitlines(), 1)
        if line.strip()
    }
    whole = {name: f" {normalized(text)} " for name, text in artifacts.items()}
    found = []
    for message_id, message in held_messages:
        plain, grams = normalized(message), trigrams(message)
        for name, text in whole.items():
            if len(plain.split()) >= CONTAINED_WORDS and f" {plain} " in text:
                found.append(f"{name} holds held-out {message_id}")
        for (name, n), line in lines.items():
            if len(grams & line) / len(grams | line) >= TOO_CLOSE:
                found.append(f"{name}:{n} is too close to held-out {message_id}")
    return found
