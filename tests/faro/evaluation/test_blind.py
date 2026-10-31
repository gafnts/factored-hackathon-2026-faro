"""
The judge's validation sample: 30 replies per language drawn by outcome, failing replies seeded per question from
replies outside the draw, each edit breaking its own point, and a sheet that shows the grader nothing of the key
(ADR-0005, Grading: validated before use; decision 6; EVL-10).
"""

import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from faro.evaluation import blind, judge, rubric
from faro.evaluation.judge import Item
from faro.evaluation.rubric import Facts

RUBRIC = rubric.load()
SEEDS = blind.load_seeds()

HANDOFF = {
    "reason_code": "unrecognized_charge",
    "queue": "dispute_intake",
    "priority": "urgent",
    "request": {"label": "block_card", "summary": "Pide bloquear por un cargo."},
    "customer_statements": ["Vio un cargo extraño."],
    "unresolved_questions": [],
    "verified_facts": [],
    "actions": [],
    "customer_sent": ["Vi un cargo extraño."],
}
REPLIES = {
    "es": {
        "answer": "Su tarjeta de crédito terminada en 1234 figura como activa, aunque su fecha de vencimiento registrada ya pasó.",
        "clarify": "¿Por qué quiere bloquear su tarjeta de crédito terminada en 1234?",
        "block": "Para bloquear su tarjeta de crédito terminada en 1234 por pérdida, confirme con el botón.",
        "hand_off": "De acuerdo: no bloqueé su tarjeta de crédito terminada en 1234.\n\n"
        "Pasé su caso a una persona del banco, que le dará seguimiento. La referencia de su caso es AB12-CD34.",
        "decline": "Este chat atiende solo sus tarjetas, así que no puedo ayudarle con esa solicitud.",
    },
    "pt": {
        "answer": "Seu cartão de crédito final 1234 consta como ativo, embora a data de validade registrada já tenha passado.",
        "clarify": "Por que você quer bloquear seu cartão de crédito final 1234?",
        "block": "Para bloquear seu cartão de crédito final 1234 por perda, confirme no botão.",
        "hand_off": "Certo: não bloqueei seu cartão de crédito final 1234.\n\n"
        "Passei seu caso para uma pessoa do banco, que vai dar continuidade a ele. A referência do seu caso é AB12-CD34.",
        "decline": "Este chat atende apenas os seus cartões, então não posso ajudar com esse pedido.",
    },
}
FACTS = {
    "answer": Facts(rules=frozenset({"POL-31", "POL-29"})),
    "clarify": Facts(rules=frozenset({"POL-35"})),
    "block": Facts(rules=frozenset({"POL-36"})),
    "hand_off": Facts(
        rules=frozenset({"POL-39"}), handoff_filed=True, confirmation_ended=True
    ),
    "decline": Facts(rules=frozenset({"POL-43"})),
}
AWAITING = {"block": "confirm_control"}


def item(n: int, language: str, outcome: str) -> Item:
    return Item(
        item_id=f"{n:016x}-t01",
        case_id=f"{n:016x}",
        turn=1,
        language=language,
        earlier=(),
        sent="Hola",
        decisions=(
            {
                "label": "block_card",
                "outcome": outcome,
                "awaiting": AWAITING.get(outcome, "none"),
                "queued": [],
                "rules": sorted(FACTS[outcome].rules),
            },
        ),
        reply=REPLIES[language][outcome],
        handoff=HANDOFF if outcome == "hand_off" else None,
        facts=FACTS[outcome],
    )


def pool(per: int = 30) -> list[Item]:
    found = []
    n = 0
    for language in ("es", "pt"):
        for outcome in REPLIES[language]:
            for _ in range(per):
                n += 1
                found.append(item(n, language, outcome))
    return found


def test_the_committed_seeds_cover_every_question_in_both_languages() -> None:
    assert blind.seed_problems(RUBRIC) == []
    assert blind.seed_problems(RUBRIC, {"edits": {"decision": {"es": [{"kind": "x"}]}}})


@pytest.mark.parametrize(
    ("sizes", "total", "expected"),
    [
        ({"a": 50, "b": 30, "c": 20}, 10, {"a": 5, "b": 3, "c": 2}),
        ({"a": 97, "b": 2, "c": 1}, 10, {"a": 8, "b": 1, "c": 1}),
        ({"a": 3, "b": 2}, 10, {"a": 3, "b": 2}),
        ({"a": 5, "b": 5, "c": 5}, 2, {"a": 1, "b": 1}),
        ({"a": 0, "b": 4}, 2, {"b": 2}),
    ],
)
def test_allocate_splits_in_proportion_with_one_each_while_it_can(
    sizes: dict[str, int], total: int, expected: dict[str, int]
) -> None:
    given = blind.allocate(sizes, total)

    assert sum(given.values()) == min(total, sum(sizes.values()))
    if total >= len([v for v in sizes.values() if v]):
        assert given == expected
    else:
        assert all(given[k] <= sizes[k] for k in given)


def test_the_draw_takes_each_language_s_count_across_the_outcomes() -> None:
    found = pool()
    first = blind.draw(found, random.Random(1), per_language=10)
    again = blind.draw(found, random.Random(1), per_language=10)

    assert first == again
    assert Counter(i.language for i in first) == {"es": 10, "pt": 10}
    assert Counter(blind.outcome(i) for i in first if i.language == "es") == {
        "answer": 2,
        "block": 2,
        "clarify": 2,
        "decline": 2,
        "hand_off": 2,
    }
    assert len({i.item_id for i in first}) == 20


@pytest.mark.parametrize("language", ["es", "pt"])
@pytest.mark.parametrize(
    ("question", "outcome", "gone", "added"),
    [
        ("language", "clarify", None, {"es": "quieres", "pt": "teu cartão"}),
        (
            "decision",
            "block",
            None,
            {"es": "ya quedó bloqueada", "pt": "já está bloqueado"},
        ),
        ("follow_up", "hand_off", "AB12-CD34", None),
        ("not_blocked", "hand_off", {"es": "no bloqueé", "pt": "não bloqueei"}, None),
        (
            "conflict",
            "answer",
            {"es": "aunque", "pt": "embora"},
            {"es": "el correcto", "pt": "o correto"},
        ),
        ("code_meaning", "answer", None, {"es": "fondos", "pt": "saldo"}),
        ("no_verdict", "decline", None, {"es": "fraude", "pt": "fraude"}),
        ("clear", "hand_off", "\n\n", {"es": "En relación", "pt": "Com relação"}),
    ],
)
def test_each_question_s_first_edit_breaks_its_point(
    language: str, question: str, outcome: str, gone: Any, added: Any
) -> None:
    original = item(1, language, outcome)
    edit = SEEDS["edits"][question][language][0]

    changed = blind.edited(original, edit)

    assert changed is not None and changed.reply != original.reply
    if gone is not None:
        assert (gone[language] if isinstance(gone, dict) else gone) not in changed.reply
    if added is not None:
        assert added[language] in changed.reply
    if question in ("follow_up", "not_blocked"):
        other = "Pasé su caso" if language == "es" else "Passei seu caso"
        assert other in changed.reply


def test_the_handoff_edits_change_the_free_text_only() -> None:
    original = item(1, "pt", "hand_off")
    invented, vague = SEEDS["edits"]["handoff_text"]["pt"]

    added = blind.edited(original, invented)
    replaced = blind.edited(original, vague)

    assert added is not None and replaced is not None
    assert added.reply == original.reply
    assert added.handoff is not None and added.handoff["request"]["summary"].startswith(
        "Pide bloquear"
    )
    assert "tres cargos" in added.handoff["request"]["summary"]
    assert (
        replaced.handoff is not None and replaced.handoff["customer_statements"] == []
    )
    assert original.handoff == HANDOFF
    assert blind.edited(item(2, "pt", "clarify"), invented) is None


def test_an_edit_that_changes_nothing_or_empties_the_reply_is_refused() -> None:
    plain = item(1, "es", "decline")

    assert (
        blind.edited(plain, {"kind": "substitute", "pairs": [["xyz", "abc"]]}) is None
    )
    assert blind.edited(plain, {"kind": "remove", "pattern": ".*"}) is None
    assert (
        blind.edited(plain, {"kind": "append_by_outcome", "texts": {"answer": "x"}})
        is None
    )


def test_seeds_come_from_replies_outside_the_draw_one_each_and_say_what_they_break() -> (
    None
):
    found = pool()
    rng = random.Random(3)
    natural = blind.draw(found, rng, per_language=10)
    taken = {i.item_id for i in natural}

    made, short = blind.seeded(
        [i for i in found if i.item_id not in taken], RUBRIC, SEEDS, rng, 4
    )

    assert short == {}
    assert Counter(i.seeded_for for i in made) == {q.id: 4 for q in RUBRIC.questions}
    sources = [i.source for i in made]
    assert len(set(sources)) == len(sources)
    assert not set(sources) & taken
    assert all(i.item_id.startswith(f"seeded-{i.seeded_for}-") for i in made)
    assert all(RUBRIC.question(str(i.seeded_for)).applies(i.facts) for i in made)
    assert Counter(i.language for i in made if i.seeded_for == "decision") == {
        "es": 2,
        "pt": 2,
    }


def test_a_question_without_replies_to_seed_is_reported_short() -> None:
    found = [item(n, "es", "clarify") for n in range(6)]

    made, short = blind.seeded(found, RUBRIC, SEEDS, random.Random(0), 2)

    assert short["follow_up"] == 2 and short["not_blocked"] == 2
    assert not any(i.seeded_for == "follow_up" for i in made)


def played(tmp_path: Path, items: list[Item], **info: Any) -> Path:
    """
    A run whose evidence the sample reads, with the judge's items standing in for what items() would find in it.
    """
    run = tmp_path / "run"
    run.mkdir()
    (run / "summary.json").write_text(
        json.dumps({"set": "regression", "mode": "in_process", **info}),
        encoding="utf-8",
    )
    return run


def test_the_sample_writes_a_blind_sheet_its_guide_and_a_key_kept_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = pool()
    monkeypatch.setattr(judge, "run_items", lambda run: found)
    run = played(tmp_path, found)
    out = tmp_path / "sample"

    manifest = blind.sample(run, out, RUBRIC, seed=5, per_language=10)

    assert manifest["validates"] is False
    assert manifest["natural"] == {"es": 10, "pt": 10}
    assert manifest["seeded_short"] == {}
    assert manifest["rows"] == 20 + 10 * len(RUBRIC.questions)
    rows = blind.read_sheet(out / "sheet.csv")
    assert [r["row"] for r in rows] == [str(n) for n in range(1, manifest["rows"] + 1)]
    sheet = (out / "sheet.csv").read_text(encoding="utf-8")
    assert (
        "seeded" not in sheet
        and "item_id" not in sheet
        and "0000000000000001" not in sheet
    )
    key = json.loads((out / "key.json").read_text(encoding="utf-8"))
    assert sum(k["seeded_for"] is not None for k in key) == 10 * len(RUBRIC.questions)
    by_row = {str(k["row"]): k for k in key}
    clarify = next(
        r
        for r in rows
        if "¿Por qué quiere" in r["reply"] and not by_row[r["row"]]["seeded_for"]
    )
    assert clarify[blind.header("follow_up", RUBRIC)] == blind.NOT_ASKED
    assert clarify[blind.header("decision", RUBRIC)] == ""
    assert "### decision" in (out / "guide.md").read_text(encoding="utf-8")
    assert set(manifest["results"]) == {"items.jsonl", "sheet.csv"}


def test_only_the_selection_set_played_end_to_end_validates(tmp_path: Path) -> None:
    run = tmp_path / "e2e"
    run.mkdir()
    (run / "manifest.json").write_text(
        json.dumps({"mode": "end_to_end", "set": {"name": "selection"}}),
        encoding="utf-8",
    )

    info = blind.run_info(run)

    assert info == {"run": "e2e", "set": "selection", "mode": "end_to_end"}


def test_a_run_without_replies_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(judge, "run_items", lambda run: [])

    with pytest.raises(blind.SampleError):
        blind.sample(played(tmp_path, []), tmp_path / "out", RUBRIC)


def test_no_question_is_seeded_below_the_bar_s_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = pool()
    monkeypatch.setattr(judge, "run_items", lambda run: found)
    run = played(tmp_path, found)

    with pytest.raises(blind.SampleError, match="at least the bar's 10"):
        blind.sample(run, tmp_path / "low", RUBRIC, per_question=9)
    more = blind.sample(run, tmp_path / "high", RUBRIC, per_question=11)

    assert set(more["seeded"].values()) == {11}
