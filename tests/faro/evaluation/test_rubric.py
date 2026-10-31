"""
The rubric keeps its rules: typed questions, each citing rules the policy holds and requirements the prerequisites
list, a bar fixed before grading, and code, not the judge, deciding which questions apply and which answer passes
(ADR-0005, Grading; EVL-10).
"""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from faro.evaluation import rubric
from faro.evaluation.rubric import Facts

DOCS = Path(__file__).resolve().parents[3] / "docs"
LOADED = rubric.load()


def test_the_committed_rubric_has_no_problem() -> None:
    assert rubric.problems() == []
    assert LOADED.version >= 1
    assert len(LOADED.sha256) == 64


def test_every_cited_rule_and_requirement_exists() -> None:
    policy = (DOCS / "policy" / "card-support.md").read_text(encoding="utf-8")
    defined = set(re.findall(r"^- \*\*(POL-\d{2})\*\*", policy, re.MULTILINE))
    listed = (DOCS / "prerequisites.md").read_text(encoding="utf-8")
    requirements = set(re.findall(r"^\| ([A-Z]{1,3}-\d{2}) \|", listed, re.MULTILINE))
    for q in LOADED.questions:
        assert set(q.rules) | set(q.cited) <= defined, q.id
        assert set(q.requirements) <= requirements, q.id


def test_only_the_clarity_score_may_be_dropped_and_go_without_a_rule() -> None:
    dropped = [q for q in LOADED.questions if q.droppable]
    assert [(q.id, q.type) for q in dropped] == [("clear", "score")]
    assert all(q.rules for q in LOADED.questions if not q.droppable)
    assert {q.type for q in LOADED.questions} == set(rubric.TYPES)


def test_code_decides_which_questions_a_turn_calls_for() -> None:
    always = {q.id for q in LOADED.questions if q.when == "always"}

    assert {q.id for q in LOADED.applicable(Facts())} == always
    filed = {q.id for q in LOADED.applicable(Facts(handoff_filed=True))}
    assert filed - always == {"follow_up", "handoff_text"}
    ended = {q.id for q in LOADED.applicable(Facts(confirmation_ended=True))}
    assert ended - always == {"not_blocked"}
    conflict = {q.id for q in LOADED.applicable(Facts(rules=frozenset({"POL-31"})))}
    assert conflict - always == {"conflict"}
    code = {q.id for q in LOADED.applicable(Facts(rules=frozenset({"POL-29"})))}
    assert code - always == {"code_meaning"}


def test_the_passing_answer_is_code_s_not_the_judge_s() -> None:
    language = LOADED.question("language")
    assert language.passes("es_usted", "es")
    assert not language.passes("es_usted", "pt")
    assert language.passes("pt_voce", "pt")
    assert not language.passes("es_tu", "es")
    decision = LOADED.question("decision")
    assert decision.passes("yes", "es") and not decision.passes("no", "es")
    clear = LOADED.question("clear")
    assert clear.passes(3, "pt") and clear.passes(5, "pt")
    assert not clear.passes(2, "pt") and not clear.passes("3", "pt")


def test_the_output_schema_asks_each_question_with_its_reason_first() -> None:
    asked = LOADED.applicable(Facts(handoff_filed=True))
    schema = LOADED.schema(asked)

    assert schema["required"] == [q.id for q in asked]
    assert schema["additionalProperties"] is False
    for q in asked:
        part = schema["properties"][q.id]
        assert list(part["properties"]) == ["reason", "answer", "confidence"]
        assert part["properties"]["answer"]["enum"] == q.values()
        assert part["properties"]["confidence"]["enum"] == list(LOADED.confidence)
    assert schema["properties"]["clear"]["properties"]["answer"]["enum"] == [
        1,
        2,
        3,
        4,
        5,
    ]
    written = json.dumps(schema)
    for unsupported in ("minimum", "maximum", "minLength", "maxLength", "maxItems"):
        assert unsupported not in written


def edited(change: dict[str, object], question: str = "language") -> list[str]:
    body = json.loads(rubric.raw())
    for q in body["questions"]:
        if q["id"] == question:
            q.update(change)
    return rubric.problems(rubric.load(json.dumps(body).encode()))


@pytest.mark.parametrize(
    ("change", "question", "found"),
    [
        ({"type": "free_text"}, "decision", "unknown type"),
        ({"rules": ["policy 5"]}, "decision", "isn't a rule ID"),
        ({"requirements": ["eval"]}, "decision", "isn't a requirement ID"),
        ({"rules": []}, "decision", "cites no rule"),
        ({"droppable": True}, "decision", "only the clarity score"),
        ({"passing": {"es": "es_usted"}}, "language", "one of its options per"),
        ({"passing_score": 9}, "clear", "off its scale"),
        ({"applies": {"when": "rules_cited"}}, "conflict", "names the rules"),
        ({"applies": {"when": "sometimes"}}, "decision", "unknown condition"),
        ({"id": "decision"}, "language", "appears twice"),
    ],
)
def test_problems_finds_a_broken_rubric(
    change: dict[str, object], question: str, found: str
) -> None:
    assert any(found in p for p in edited(change, question))


def test_problems_finds_a_broken_bar() -> None:
    broken = replace(LOADED, bar={"agreement": 90, "kappa": 0.6}, by_hand=("high",))
    found = rubric.problems(broken)

    assert any("aren't shares" in p for p in found)
    assert any("replies that deserve a no" in p for p in found)
    assert any("highest confidence level" in p for p in found)
