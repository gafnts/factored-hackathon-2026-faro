"""
Masks the card numbers a customer types before anything stores them or sends them to a model (POL-11; ADR-0004, Card
numbers): a run of 13 or more digits, spaced, dashed, or neither, keeps its last four. It is the pattern the handoff
schema and the execution record reject. A decimal digit of any script counts as its ASCII digit, and invisible format
characters are dropped first, so neither hides a number from the pattern. A number spelled out in words, split across
messages, or separated by other characters still passes (decision 11).
"""

import re
import unicodedata

DIGIT_RUN = "[0-9](?:[ -]?[0-9]){12,}"

_DIGIT_RUN = re.compile(DIGIT_RUN)
_NOT_DIGIT = re.compile("[^0-9]")


def normalized(text: str) -> str:
    return "".join(
        str(unicodedata.decimal(c)) if c.isdecimal() else c
        for c in text
        if unicodedata.category(c) != "Cf"
    )


def mask(text: str) -> str:
    return _DIGIT_RUN.sub(
        lambda match: "****" + _NOT_DIGIT.sub("", match.group())[-4:], normalized(text)
    )


def has_digit_run(text: str) -> bool:
    return _DIGIT_RUN.search(normalized(text)) is not None
