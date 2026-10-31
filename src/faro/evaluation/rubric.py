"""
The judge's rubric (ADR-0005, Grading; EVL-10): typed questions for what code can't compare, each citing the rules
that require it, read from rubric.json. A question is a choice, a yes or no, or a score; code decides which questions
a reply's turn calls for (applies), and which answer passes (passes), so the judge never sees what we expect. Every
answer carries the judge's confidence, and the levels in `by_hand` send the judgment to a person. The bar a question
must clear before the judge grades it is fixed here, with the version, before any grading. `problems()` lists every
way the file breaks these rules; the tests require it to find none.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

TYPES = ("choice", "yes_no", "score")
WHEN = ("always", "handoff_filed", "confirmation_ended", "rules_cited")
LANGUAGES = ("es", "pt")
RULE = re.compile(r"^POL-\d{2}$")
REQUIREMENT = re.compile(r"^(?:[A-Z]{2,3}-\d{2}|M-\d{2})$")


@dataclass(frozen=True)
class Facts:
    """
    What code knows about a reply's turn that decides which questions apply: the rules its decisions cite, whether it
    filed a handoff, and whether a confirmation ended unused in it (cancelled or lapsed).
    """

    rules: frozenset[str] = frozenset()
    handoff_filed: bool = False
    confirmation_ended: bool = False


@dataclass(frozen=True)
class Question:
    id: str
    type: str
    text: str
    when: str
    rules: tuple[str, ...]
    requirements: tuple[str, ...]
    cited: tuple[str, ...] = ()
    options: Mapping[str, str] = field(default_factory=dict)
    passing: Mapping[str, str] = field(default_factory=dict)
    scale: tuple[int, int] = (1, 5)
    passing_score: int | None = None
    droppable: bool = False

    def applies(self, facts: Facts) -> bool:
        if self.when == "handoff_filed":
            return facts.handoff_filed
        if self.when == "confirmation_ended":
            return facts.confirmation_ended
        if self.when == "rules_cited":
            return bool(facts.rules & set(self.cited))
        return True

    def values(self) -> list[Any]:
        if self.type == "choice":
            return list(self.options)
        if self.type == "score":
            return list(range(self.scale[0], self.scale[1] + 1))
        return ["yes", "no"]

    def passes(self, answer: Any, language: str) -> bool:
        """
        language is the one the turn decided to reply in (POL-50), which picks a choice's passing option.
        """
        if self.type == "choice":
            return bool(answer == self.passing.get(language))
        if self.type == "score":
            assert self.passing_score is not None
            return isinstance(answer, int) and answer >= self.passing_score
        return bool(answer == "yes")


@dataclass(frozen=True)
class Rubric:
    version: int
    sha256: str
    confidence: tuple[str, ...]
    by_hand: tuple[str, ...]
    bar: Mapping[str, Any]
    questions: tuple[Question, ...]

    def question(self, question_id: str) -> Question:
        return next(q for q in self.questions if q.id == question_id)

    def applicable(self, facts: Facts) -> tuple[Question, ...]:
        return tuple(q for q in self.questions if q.applies(facts))

    def schema(self, questions: Iterable[Question]) -> dict[str, Any]:
        """
        The judge's structured output for a reply: one object per question asked, its reason first, so the judge
        writes it before it answers. Only the keywords structured output accepts are used.
        """
        asked = list(questions)
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [q.id for q in asked],
            "properties": {
                q.id: {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["reason", "answer", "confidence"],
                    "properties": {
                        "reason": {"type": "string"},
                        "answer": {"enum": q.values()},
                        "confidence": {"enum": list(self.confidence)},
                    },
                }
                for q in asked
            },
        }


def _question(body: Mapping[str, Any]) -> Question:
    applies = body["applies"]
    scale = body.get("scale", (1, 5))
    return Question(
        id=body["id"],
        type=body["type"],
        text=body["text"],
        when=applies["when"],
        cited=tuple(applies.get("rules", ())),
        rules=tuple(body["rules"]),
        requirements=tuple(body["requirements"]),
        options=dict(body.get("options", {})),
        passing=dict(body.get("passing", {})),
        scale=(int(scale[0]), int(scale[1])),
        passing_score=body.get("passing_score"),
        droppable=bool(body.get("droppable", False)),
    )


def raw() -> bytes:
    return files(__package__).joinpath("rubric.json").read_bytes()


def load(text: bytes | None = None) -> Rubric:
    body = raw() if text is None else text
    data = json.loads(body)
    return Rubric(
        version=data["version"],
        sha256=hashlib.sha256(body).hexdigest(),
        confidence=tuple(data["confidence"]),
        by_hand=tuple(data["by_hand"]),
        bar=dict(data["bar"]),
        questions=tuple(_question(q) for q in data["questions"]),
    )


def _question_problems(q: Question) -> Iterator[str]:
    if q.type not in TYPES:
        yield f"{q.id} has an unknown type {q.type!r}"
    if q.when not in WHEN:
        yield f"{q.id} applies on an unknown condition {q.when!r}"
    if (q.when == "rules_cited") != bool(q.cited):
        yield f"{q.id} names the rules it applies on only when it applies on cited rules"
    if any(not RULE.match(r) for r in (*q.rules, *q.cited)):
        yield f"{q.id} cites something that isn't a rule ID"
    if any(not REQUIREMENT.match(r) for r in q.requirements):
        yield f"{q.id} cites something that isn't a requirement ID"
    if not q.rules and not q.droppable:
        yield f"{q.id} cites no rule, which only the droppable question may"
    if q.droppable and q.type != "score":
        yield f"{q.id} may be dropped, which only the clarity score may"
    if q.type == "choice":
        if len(q.options) < 2:
            yield f"{q.id} offers fewer than two options"
        if set(q.passing) != set(LANGUAGES) or not set(q.passing.values()) <= set(
            q.options
        ):
            yield f"{q.id} doesn't pass exactly one of its options per language"
    elif q.options or q.passing:
        yield f"{q.id} has options but isn't a choice"
    if q.type == "score":
        low, high = q.scale
        if not low < high or q.passing_score is None:
            yield f"{q.id} has no scale or no passing score"
        elif not low < q.passing_score <= high:
            yield f"{q.id} passes at a score off its scale"
    elif q.passing_score is not None:
        yield f"{q.id} has a passing score but isn't a score"


def problems(rubric: Rubric | None = None) -> list[str]:
    rubric = load() if rubric is None else rubric
    found: list[str] = []
    ids = [q.id for q in rubric.questions]
    found += [
        f"{i} appears twice" for i in sorted({i for i in ids if ids.count(i) > 1})
    ]
    for q in rubric.questions:
        found += _question_problems(q)
    if not rubric.confidence:
        found.append("the rubric names no confidence levels")
    if not set(rubric.by_hand) <= set(rubric.confidence):
        found.append("a level graded by hand isn't a confidence level")
    if rubric.confidence and rubric.confidence[0] in rubric.by_hand:
        found.append("the highest confidence level is graded by hand")
    bar = rubric.bar
    if not (0 < bar.get("agreement", 0) <= 1 and 0 < bar.get("kappa", 0) <= 1):
        found.append("the bar's agreement and kappa aren't shares")
    if not isinstance(bar.get("deserve_no"), int) or bar["deserve_no"] < 1:
        found.append("the bar names no count of replies that deserve a no")
    if sum(q.droppable for q in rubric.questions) > 1:
        found.append("more than one question may be dropped")
    return found
