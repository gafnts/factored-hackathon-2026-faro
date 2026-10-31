"""
The reply check (ADR-0004, decision 8, and its amendment of 2026-10-01): a read's answer that the model wrote with
placeholders reaches the customer only if every placeholder names one of its facts, every fact its fixed reply states is
there, no digit stands outside a placeholder, nothing names the bank's internal flags, no word names a closed,
suspended, or inactive status outside a placeholder, and a list's placeholder stands alone on its line, once, a
decline's reason right under its transaction, while no other placeholder does. The filled text must hold no run of 13
or more digits. A failure is named by its rule, never by the text that broke it (POL-11, POL-12, POL-18, POL-40).
"""

import re

from faro.agent.texts import PLACEHOLDER, fill
from faro.masking import has_digit_run, normalized

FLAGS = re.compile(r"is_fraud|fraud_score", re.IGNORECASE)
# A card's status reaches the reply through its placeholder only, so the customer's own is never named (POL-12).
WITHHELD = re.compile(
    r"suspend|suspens|cerrad|encerrad|closed|inactiv|inativ", re.IGNORECASE
)
DIGIT = re.compile(r"[0-9]")
BRACE = re.compile(r"[{}]")
# Their values are lines that start with a dash, which shared with other text read as figures in a sentence. Any
# other fact alone on a line, out of its sentence, doesn't say what it is.
LISTS = (
    "cards",
    "card_list",
    "credits",
    "transactions",
    "transaction",
    "transaction.meaning",
)
ALONE = re.compile(r"\s*\{[a-z_]+(?:\.[a-z_]+)?\}\s*")
# With no blank line between them, the reason and its transaction are one list, which the chat draws as one frame.
UNDER = re.compile(r"\{transaction\}[ \t]*\n[ \t]*\{transaction\.meaning\}")


def failures(text: str, facts: dict[str, str]) -> list[str]:
    """
    The rules the model's text breaks, given the facts its placeholders may name, all of which it must name.
    """
    found: list[str] = []
    named = PLACEHOLDER.findall(text)
    words = normalized(PLACEHOLDER.sub(" ", text))
    if set(named) - facts.keys() or BRACE.search(words):
        found.append("unknown_placeholder")
    if facts.keys() - set(named):
        found.append("missing_fact")
    if DIGIT.search(words):
        found.append("bare_number")
    if "unknown_placeholder" not in found and has_digit_run(fill(text, facts)):
        found.append("digit_run")
    if FLAGS.search(text):
        found.append("internal_flag")
    if WITHHELD.search(words):
        found.append("withheld_status")
    lines = text.splitlines()
    shared = [line for line in lines if not ALONE.fullmatch(line)]
    if any(name in LISTS for line in shared for name in PLACEHOLDER.findall(line)):
        found.append("inline_list")
    alone = [line for line in lines if ALONE.fullmatch(line)]
    if any(name not in LISTS for line in alone for name in PLACEHOLDER.findall(line)):
        found.append("lone_fact")
    if "transaction.meaning" in named and not UNDER.search(text):
        found.append("reason_apart")
    # Each copy states every item again.
    if any(named.count(name) > 1 for name in LISTS):
        found.append("repeated_list")
    return found
