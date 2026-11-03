"""
The outside source's requests word every situation the generator plays, each makes a family that keeps the families'
rules, and neither the guide nor the requests hold a message of ours (ADR-0011, Wording sources; DML-09).
"""

import re

import pytest

from faro.evaluation import bronze, families, generator
from faro.evaluation.facts import contract_words
from faro.evaluation.outside import (
    ANSWERS,
    PHRASINGS,
    answer_bodies,
    family_body,
    guide,
    requests,
)

from .bank import Bank

ASKED, ANSWERED = requests()
WORDS = {
    "es": ("mi", "tarjeta", "uno", "dos", "tres", "cuatro", "cinco", "seis"),
    "pt": ("meu", "cartão", "um", "dois", "três", "quatro", "cinco", "seis"),
    "en": ("my", "card", "one", "two", "three", "four", "five", "six"),
    "fr": ("ma", "carte", "un", "deux", "trois", "quatre", "cinq", "six"),
}


def written(language: str, slots: tuple[str, ...], count: int) -> list[str]:
    """
    Placeholder phrasings in the language, each holding the slots once.
    """
    own, card, *numbers = WORDS[language]
    held = " ".join(f"{{{slot}}}" for slot in slots)
    return [f"{own} {card} {held} {numbers[n]}".strip() for n in range(count)]


def placeholder_source() -> tuple[list[families.Family], list[families.Answer]]:
    loaded = [
        families._family(
            request.group,
            family_body(
                request,
                {
                    language: written(language, request.slots, PHRASINGS)
                    for language in request.languages
                },
                "test",
            ),
            families.OUTSIDE,
        )
        for request in ASKED
    ]
    answers = [
        families._answer(body, families.OUTSIDE)
        for request in ANSWERED
        for body in answer_bodies(
            request,
            {
                language: written(language, request.slots, ANSWERS)
                for language in families.LANGUAGES
            },
            "test",
        )
    ]
    return loaded, answers


def test_every_request_makes_a_family_that_keeps_the_rules() -> None:
    loaded, answers = placeholder_source()

    found = [p for f in loaded for p in families._family_problems(f)]
    found += [p for a in answers for p in families._answer_problems(a)]

    assert found == []
    assert len({f.family_id for f in loaded}) == len(ASKED)
    assert len({a.answer_id for a in answers}) == ANSWERS * len(ANSWERED)


def test_every_group_has_four_families_or_more() -> None:
    for group in families.GROUPS:
        assert sum(r.group == group for r in ASKED) >= 4, group


@pytest.mark.xdist_group("evaluation_bank")
def test_the_requests_word_every_situation(bank: Bank) -> None:
    loaded, answers = placeholder_source()
    with bronze.connect(bank.database, "held_out") as con:
        drawing = generator.Generator(
            con,
            "held_out",
            loaded,
            answers,
            families.held_out_ids(loaded, answers),
            contract_words(),
        )
        unworded = [
            f"{s.name} in {language}"
            for s in generator.SITUATIONS
            for language in s.languages
            if not drawing._worded(s, language)
        ]

    assert unworded == []


def test_neither_the_guide_nor_the_requests_hold_a_message_of_ours() -> None:
    ours = [m.text for f in families.load() for m in f.messages]
    ours += [t for a in families.load_answers() for t in a.texts.values()]
    lines = [line for line in guide().splitlines() if line.strip()]
    lines += [r.says for r in ASKED] + [r.says for r in ANSWERED]
    every = f" {' '.join(families.normalized(line) for line in lines)} "

    close = [
        (line, text)
        for line in lines
        for text in ours
        if families.similarity(line, text) >= families.TOO_CLOSE
    ]
    quoted = [
        text
        for text in ours
        if len(families.normalized(text).split()) >= 3
        and re.search(rf" {re.escape(families.normalized(text))} ", every)
    ]

    assert close == []
    assert quoted == []
