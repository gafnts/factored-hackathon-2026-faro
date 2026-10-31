"""
The request families and the scripted customer's answers keep their rules: no value from the records, slots as
declared, the language they're filed under, a third of each group held out, and no held-out message close to a
development one (ADR-0005, decision 2, as amended on 2026-10-01; DML-09, SEC-02).
"""

import pytest

from faro.evaluation import families
from faro.evaluation.families import Answer, Family, Message


def family(
    texts: dict[str, list[str]],
    unclear: dict[str, tuple[int, ...]] | None = None,
    **fields: object,
) -> Family:
    marked = unclear or {}
    base: dict[str, object] = {
        "family_id": "card_status-01",
        "group": "card_status",
        "kind": "plain",
        "labels": ("card_status",),
        "messages": tuple(
            Message(
                f"card_status-01/{lang}/{n}",
                lang,
                "team",
                text,
                clear=n not in marked.get(lang, ()),
            )
            for lang, listed in texts.items()
            for n, text in enumerate(listed)
        ),
        "unclear": marked,
    }
    return Family(**{**base, **fields})  # type: ignore[arg-type]


SPANISH = [
    "¿Mi tarjeta está activa?",
    "Quiero el estado de mi tarjeta.",
    "¿Cómo está mi tarjeta?",
    "Dígame si mi tarjeta funciona.",
]
PORTUGUESE = [
    "Meu cartão está ativo?",
    "Quero a situação do meu cartão.",
    "Como está o meu cartão?",
    "Me diga se o meu cartão funciona.",
]


def test_the_families_and_answers_break_no_rule() -> None:
    assert families.problems() == []


def test_every_group_holds_out_a_third_of_its_families() -> None:
    loaded, answers = families.load(), families.load_answers()
    held = families.held_out_ids(loaded, answers)

    for group in families.GROUPS:
        assert sum(f.family_id in held for f in loaded if f.group == group) == 4
    for kind in families.ANSWER_KINDS:
        assert sum(a.answer_id in held for a in answers if a.kind == kind) == 1


def test_the_seed_is_ours_and_the_rest_are_paraphrases() -> None:
    first = families.load()[0]

    assert [m.author for m in first.in_language("es")][:2] == [
        families.SEED_AUTHOR,
        families.PARAPHRASE_AUTHOR,
    ]
    assert first.messages[0].id == f"{first.family_id}/es/0"


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Mi tarjeta terminada en 4821 está activa?", "digit outside a slot"),
        ("Soy CLI-AB12CD34EF56, ¿mi tarjeta?", "shaped like an identifier"),
        ("¿Mi tarjeta {last_four} está activa?", "uses slots"),
        ("Meu cartão está ativo, você sabe?", "reads as pt"),
    ],
)
def test_a_message_that_breaks_a_rule_is_named(text: str, found: str) -> None:
    broken = family({"es": [text, *SPANISH[1:]], "pt": PORTUGUESE})

    assert any(found in p for p in families._family_problems(broken))


def test_a_marked_message_is_unclear_and_exempt_from_the_language_check() -> None:
    texts = {"es": SPANISH, "pt": [PORTUGUESE[0], "mi tarjeta", *PORTUGUESE[2:]]}
    unmarked = family(texts)
    marked = family(texts, unclear={"pt": (1,)})

    assert any("reads as es" in p for p in families._family_problems(unmarked))
    assert not any("reads as es" in p for p in families._family_problems(marked))
    assert [m.clear for m in marked.in_language("pt")] == [True, False, True, True]


def test_a_mark_on_a_message_the_family_lacks_or_a_third_language_is_named() -> None:
    missing = family({"es": SPANISH, "pt": PORTUGUESE}, unclear={"pt": (9,)})
    third = family(
        {"en": ["Hello, do you speak English?"]},
        unclear={"en": (0,)},
        family_id="none-10",
        group="none",
        kind="third_language",
        labels=(),
    )

    assert any("doesn't have" in p for p in families._family_problems(missing))
    assert any("third language" in p for p in families._family_problems(third))


def test_the_words_both_languages_share_are_marked_unclear() -> None:
    loaded = {f.family_id: f for f in families.load()}
    marked = [m.id for f in loaded.values() for m in f.messages if not m.clear]

    assert "block_card-07/pt/2" in marked
    assert all(loaded[i.split("/")[0]].kind == "terse" for i in marked)


def test_a_family_short_of_messages_or_out_of_order_is_named() -> None:
    short = family({"es": SPANISH[:2], "pt": PORTUGUESE})
    unordered = family(
        {"es": SPANISH, "pt": PORTUGUESE},
        kind="multi_request",
        labels=("card_status", "block_card"),
    )

    assert any("2 es messages" in p for p in families._family_problems(short))
    assert any("POL-05" in p for p in families._family_problems(unordered))


def test_an_extraction_the_graph_doesnt_make_is_named() -> None:
    odd = family({"es": SPANISH, "pt": PORTUGUESE}, extract={"service": "mortgage"})

    assert any("doesn't" in p for p in families._family_problems(odd))


def test_a_held_out_message_close_to_a_development_one_is_named() -> None:
    held = [("h", "¿Cuál es el estado de mi tarjeta?")]
    development = [("d", "¿Cuál es el estado de mi tarjeta de crédito?")]

    assert list(families._too_close(held, development)) == [
        "held-out h is too close to development d"
    ]
    assert families.similarity(held[0][1], "Bloquee la tarjeta.") < families.TOO_CLOSE


def test_an_answer_must_give_exactly_its_kinds_slots() -> None:
    answer = Answer(
        "card_both-09", "card_both", {"es": "La {last_four}.", "pt": "O {last_four}."}
    )

    assert len(list(families._answer_problems(answer))) == 2
