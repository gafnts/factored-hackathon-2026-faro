"""
The outside source's guide and requests (ADR-0011, Wording sources): what an OpenAI model is asked to write, held-out
wording that neither we nor our model family wrote. guide.md says what the labels, kinds, extraction fields, and slots
mean; requests.json asks for a family per shape the situations play, each with what its message says, and five answers
per kind of answer they ask for. Neither holds a message of ours. A written family takes its labels, kind, extraction,
slots, relative date, and complaint from its request, never from the model, so it fits the situations its request was
written for, and the model writes only the text. The source is kept under data/evaluation/sources/outside/, out of the
repository until the first play of a set drawn from it (ADR-0011, The seal).
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

from faro.evaluation import families

# A first message and five more in each language, as our families have; five answers per kind (held-out-2.md).
PHRASINGS = 6
ANSWERS = 5


@dataclass(frozen=True)
class FamilyRequest:
    family_id: str
    group: str
    kind: str
    says: str
    labels: tuple[str, ...]
    extract: Mapping[str, str] = field(default_factory=dict)
    slots: tuple[str, ...] = ()
    when: str | None = None
    complaint: bool = False
    languages: tuple[str, ...] = families.LANGUAGES


@dataclass(frozen=True)
class AnswerRequest:
    kind: str
    says: str

    @property
    def slots(self) -> tuple[str, ...]:
        return tuple(sorted(families.ANSWER_KINDS[self.kind]))


def guide() -> str:
    return files(__package__).joinpath("guide.md").read_text(encoding="utf-8")


def requests() -> tuple[tuple[FamilyRequest, ...], tuple[AnswerRequest, ...]]:
    loaded = json.loads(
        files(__package__).joinpath("requests.json").read_text(encoding="utf-8")
    )
    numbered: dict[str, int] = {}
    asked = []
    for body in loaded["families"]:
        group = body["group"]
        numbered[group] = numbered.get(group, 0) + 1
        asked.append(
            FamilyRequest(
                family_id=f"{families.OUTSIDE}.{group}-{numbered[group]:02d}",
                group=group,
                kind=body["kind"],
                says=body["says"],
                labels=tuple(
                    body.get("labels", () if group == families.NONE else (group,))
                ),
                extract=dict(body.get("extract", {})),
                slots=tuple(body.get("slots", ())),
                when=body.get("when"),
                complaint=body.get("complaint", False),
                languages=tuple(body.get("languages", families.LANGUAGES)),
            )
        )
    answers = tuple(AnswerRequest(a["kind"], a["says"]) for a in loaded["answers"])
    return tuple(asked), answers


def family_body(
    request: FamilyRequest, texts: Mapping[str, Sequence[str]], author: str
) -> dict[str, Any]:
    """
    The written family as load_source reads it: the request's fields, and the model's text in each language.
    """
    body: dict[str, Any] = {
        "family_id": request.family_id,
        "kind": request.kind,
        "labels": list(request.labels),
        "author": author,
    }
    if request.extract:
        body["extract"] = dict(request.extract)
    if request.slots:
        body["slots"] = list(request.slots)
    if request.when is not None:
        body["when"] = request.when
    if request.complaint:
        body["complaint"] = True
    return body | {language: list(texts[language]) for language in request.languages}


def answer_bodies(
    request: AnswerRequest, texts: Mapping[str, Sequence[str]], author: str
) -> list[dict[str, Any]]:
    """
    The written answers as load_source reads them: the nth phrasing in each language makes the nth answer.
    """
    return [
        {
            "answer_id": f"{families.OUTSIDE}.{request.kind}-{n:02d}",
            "kind": request.kind,
            "author": author,
        }
        | {language: texts[language][n - 1] for language in families.LANGUAGES}
        for n in range(1, ANSWERS + 1)
    ]
