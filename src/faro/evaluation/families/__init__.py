"""
The request families and the scripted customer's answers (ADR-0005, decision 2, as amended on 2026-10-01), all
team-generated (SEC-02). A family is one request in Spanish and Portuguese: in each language's list the first message is
the seed we wrote, and the rest are paraphrases Claude Opus 5.5 wrote from it. Values are slots that the generator fills
from a case's customer, so no message holds an identifier or a value from the records. A family's `extract` is what
the agent's extraction should read from it (the graph's fields), and `when` the relative date it names. `unclear`
marks, by position in each language's list, the paraphrases that aren't clearly in one language (a bare word both
languages share): the scripted models say so, and the oracle keeps the conversation's language for the turn they open
(POL-50; ADR-0005's amendment of 2026-10-02).

Each label's families and each kind of answer are split by `split.held_out_families`: a third held out, the rest
development. `problems()` lists every way the files break these rules; the tests require it to find none.
"""

import json
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from itertools import product
from typing import Any

from faro.split import held_out_families

# POL-05's order.
LABELS = (
    "block_card",
    "unrecognized_charge",
    "talk_to_human",
    "decline_reason",
    "card_status",
    "available_credit",
    "recent_transactions",
    "unsupported",
)
NONE = "none"
GROUPS = (*LABELS, NONE)
PER_GROUP = 12
LANGUAGES = ("es", "pt")
OTHER_LANGUAGES = ("en", "fr")
KINDS = frozenset(
    {
        "plain",
        "terse",
        "indirect",
        "access",
        "multi_request",
        "injection",
        "mixed_language",
        "follow_up",
        "no_request",
        "third_language",
    }
)
SLOTS = frozenset({"last_four", "merchant", "amount", "date", "other_customer_id"})
EXTRACT = {
    "card_type": frozenset({"credit", "debit"}),
    "block_reason": frozenset(
        {"lost", "stolen", "unrecognized_charge", "customer_request"}
    ),
    "cards": frozenset({"all"}),
    "page": frozenset({"next", "earlier"}),
    "owner": frozenset({"someone_else"}),
    "conflict": frozenset({"asks_which"}),
    "service": frozenset(
        {
            "unblock",
            "replacement",
            "pin",
            "limit_increase",
            "other_card_service",
            "outside_cards",
        }
    ),
}
WHEN = frozenset({"today", "yesterday", "day_before"})
ANSWER_KINDS = {
    "card_last_four": {"last_four"},
    "card_type": {"card_type"},
    "card_both": {"card_type", "last_four"},
    "card_position": set(),
    "reason_lost": set(),
    "reason_stolen": set(),
    "reason_unrecognized_charge": set(),
    "reason_customer_request": set(),
    "transaction_merchant": {"merchant"},
    "transaction_amount": {"amount"},
    "transaction_date": {"date"},
    "transaction_newest": set(),
    "dont_know": set(),
    "aside": set(),
    "typed_yes": set(),
}
PER_ANSWER_KIND = 3
SEED_AUTHOR = "team"
PARAPHRASE_AUTHOR = "claude-opus-5-5"
MAX_LENGTH = 300
MIN_MESSAGES = 4
# ADR-0005's bar for a message too close to another to count as unseen.
TOO_CLOSE = 0.6

SLOT = re.compile(r"\{([a-z_]+)\}")
IDENTIFIER = re.compile(r"(CLI|PRD|TRX|BRN)-", re.IGNORECASE)
# Words that mark one language and not the other; enough to catch a message filed under the wrong one.
MARKERS = {
    "es": frozenset(
        [
            "mi",
            "mis",
            "tarjeta",
            "tarjetas",
            "el",
            "la",
            "lo",
            "le",
            "los",
            "las",
            "del",
            "su",
            "sus",
            "una",
            "qué",
            "cuál",
            "cuánto",
            "cuándo",
            "quiero",
            "quisiera",
            "necesito",
            "usted",
            "y",
            "ayer",
            "cobro",
            "cargo",
            "bloquee",
            "rechazaron",
            "hablar",
            "dígame",
            "gracias",
            "hola",
            "también",
            "después",
        ]
    ),
    "pt": frozenset(
        [
            "meu",
            "minha",
            "meus",
            "minhas",
            "cartão",
            "cartões",
            "o",
            "os",
            "as",
            "do",
            "da",
            "dos",
            "das",
            "um",
            "uma",
            "qual",
            "quanto",
            "quando",
            "quero",
            "queria",
            "preciso",
            "você",
            "e",
            "ontem",
            "cobrança",
            "bloqueie",
            "recusaram",
            "falar",
            "obrigado",
            "obrigada",
            "oi",
            "não",
            "também",
            "depois",
        ]
    ),
}


@dataclass(frozen=True)
class Message:
    id: str
    language: str
    author: str
    text: str
    clear: bool = True


@dataclass(frozen=True)
class Family:
    family_id: str
    group: str
    kind: str
    labels: tuple[str, ...]
    messages: tuple[Message, ...]
    extract: Mapping[str, str] = field(default_factory=dict)
    slots: frozenset[str] = frozenset()
    when: str | None = None
    complaint: bool = False
    unclear: Mapping[str, tuple[int, ...]] = field(default_factory=dict)

    def in_language(self, language: str) -> tuple[Message, ...]:
        return tuple(m for m in self.messages if m.language == language)


@dataclass(frozen=True)
class Answer:
    answer_id: str
    kind: str
    texts: Mapping[str, str]


def _messages(family_id: str, body: Mapping[str, Any]) -> Iterator[Message]:
    unclear = body.get("unclear", {})
    for language in (*LANGUAGES, *OTHER_LANGUAGES):
        for n, text in enumerate(body.get(language, ())):
            author = SEED_AUTHOR if n == 0 else PARAPHRASE_AUTHOR
            yield Message(
                f"{family_id}/{language}/{n}",
                language,
                author,
                text,
                clear=n not in unclear.get(language, ()),
            )


def _family(group: str, body: Mapping[str, Any]) -> Family:
    return Family(
        family_id=body["family_id"],
        group=group,
        kind=body["kind"],
        labels=tuple(body.get("labels", ())),
        messages=tuple(_messages(body["family_id"], body)),
        extract=dict(body.get("extract", {})),
        slots=frozenset(body.get("slots", ())),
        when=body.get("when"),
        complaint=bool(body.get("complaint", False)),
        unclear={k: tuple(v) for k, v in body.get("unclear", {}).items()},
    )


def _read(name: str) -> Any:
    return json.loads(
        files(__package__).joinpath(f"{name}.json").read_text(encoding="utf-8")
    )


def load() -> tuple[Family, ...]:
    return tuple(
        _family(group, body) for group in GROUPS for body in _read(group)["families"]
    )


def load_answers() -> tuple[Answer, ...]:
    return tuple(
        Answer(a["answer_id"], a["kind"], {k: a[k] for k in LANGUAGES})
        for a in _read("answers")["answers"]
    )


def held_out_ids(
    families: Sequence[Family], answers: Sequence[Answer] = ()
) -> frozenset[str]:
    """
    The families and answers on the held-out side, by the shared split over each group.
    """
    chosen: set[str] = set()
    for group in GROUPS:
        chosen |= held_out_families(f.family_id for f in families if f.group == group)
    for kind in ANSWER_KINDS:
        chosen |= held_out_families(a.answer_id for a in answers if a.kind == kind)
    return frozenset(chosen)


def normalized(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in folded if not unicodedata.combining(c))
    plain = SLOT.sub(" § ", plain)
    return " ".join(re.sub(r"[^a-z0-9§ ]", " ", plain).split())


def trigrams(text: str) -> frozenset[str]:
    padded = f"  {normalized(text)}  "
    return frozenset(padded[i : i + 3] for i in range(len(padded) - 2))


def similarity(a: str, b: str) -> float:
    x, y = trigrams(a), trigrams(b)
    return len(x & y) / len(x | y) if x | y else 1.0


def _words(text: str) -> list[str]:
    return re.findall(r"[a-záéíóúâêôãõçñü]+", text.lower())


def _language_problem(message: Message) -> str | None:
    if message.language not in MARKERS:
        return None
    other = "pt" if message.language == "es" else "es"
    words = _words(message.text)
    own = sum(w in MARKERS[message.language] and w not in MARKERS[other] for w in words)
    theirs = sum(
        w in MARKERS[other] and w not in MARKERS[message.language] for w in words
    )
    if theirs > own:
        return f"{message.id} reads as {other}"
    return None


def _message_problems(family: Family, message: Message) -> Iterator[str]:
    used = set(SLOT.findall(message.text))
    if used != family.slots:
        yield f"{message.id} uses slots {sorted(used)}, not {sorted(family.slots)}"
    bare = SLOT.sub("", message.text)
    if re.search(r"\d", bare):
        yield f"{message.id} has a digit outside a slot"
    if "{" in bare or "}" in bare:
        yield f"{message.id} has a stray brace"
    if IDENTIFIER.search(bare):
        yield f"{message.id} has something shaped like an identifier"
    if not 1 <= len(message.text) <= MAX_LENGTH:
        yield f"{message.id} is {len(message.text)} characters long"
    if (
        family.kind not in ("mixed_language", "terse", "third_language")
        and message.clear
    ):
        problem = _language_problem(message)
        if problem:
            yield problem


def _family_problems(family: Family) -> Iterator[str]:
    fid = family.family_id
    if not re.fullmatch(rf"{family.group}-\d{{2}}", fid):
        yield f"{fid} isn't named after its group"
    if family.kind not in KINDS:
        yield f"{fid} has an unknown kind {family.kind!r}"
    if any(label not in LABELS for label in family.labels):
        yield f"{fid} has a label outside the policy's eight"
    if list(family.labels) != sorted(family.labels, key=LABELS.index):
        yield f"{fid}'s labels aren't in POL-05's order"
    if family.group == NONE:
        if family.labels:
            yield f"{fid} holds no card request, so it takes no label"
    elif family.group not in family.labels:
        yield f"{fid} doesn't carry its own label"
    if (family.kind == "multi_request") != (len(family.labels) > 1):
        yield f"{fid}'s kind and label count disagree"
    for name, value in family.extract.items():
        if value not in EXTRACT.get(name, ()):
            yield f"{fid} extracts {name}={value!r}, which the graph doesn't"
    if family.slots - SLOTS:
        yield f"{fid} has unknown slots"
    if family.when is not None and family.when not in WHEN:
        yield f"{fid}'s relative date isn't one the oracle resolves"
    if family.complaint and "talk_to_human" not in family.labels:
        yield f"{fid} is a complaint without talk_to_human"
    for language, positions in family.unclear.items():
        held = family.in_language(language) if language in LANGUAGES else ()
        if any(p not in range(len(held)) for p in positions):
            yield f"{fid} marks a message it doesn't have as unclear"
    if family.kind == "third_language" and family.unclear:
        yield f"{fid} is in a third language, which no mark makes unclear"
    languages = OTHER_LANGUAGES if family.kind == "third_language" else LANGUAGES
    for language in languages:
        held = family.in_language(language)
        if family.kind != "third_language" and len(held) < MIN_MESSAGES:
            yield f"{fid} has {len(held)} {language} messages"
        seen: set[str] = set()
        for message in held:
            key = normalized(message.text)
            if key in seen:
                yield f"{message.id} repeats another message of its family"
            seen.add(key)
    if family.kind == "third_language" and not family.messages:
        yield f"{fid} has no message"
    if family.kind != "third_language" and any(
        m.language in OTHER_LANGUAGES for m in family.messages
    ):
        yield f"{fid} has a third language outside a third_language family"
    for message in family.messages:
        yield from _message_problems(family, message)


def _answer_problems(answer: Answer) -> Iterator[str]:
    if answer.kind not in ANSWER_KINDS:
        yield f"{answer.answer_id} has an unknown kind"
        return
    if not re.fullmatch(rf"{answer.kind}-\d{{2}}", answer.answer_id):
        yield f"{answer.answer_id} isn't named after its kind"
    for language, text in answer.texts.items():
        if set(SLOT.findall(text)) != ANSWER_KINDS[answer.kind]:
            yield f"{answer.answer_id}/{language} doesn't give exactly its kind's slots"
        bare = SLOT.sub("", text)
        if re.search(r"\d", bare) or IDENTIFIER.search(bare):
            yield f"{answer.answer_id}/{language} holds a value outside a slot"


def _too_close(
    held: Iterable[tuple[str, str]], development: Iterable[tuple[str, str]]
) -> Iterator[str]:
    grams = {k: trigrams(t) for k, t in [*held, *development]}
    for (h, _), (d, _) in product(list(held), list(development)):
        x, y = grams[h], grams[d]
        if len(x & y) / len(x | y) >= TOO_CLOSE:
            yield f"held-out {h} is too close to development {d}"


def texts(
    families: Sequence[Family], answers: Sequence[Answer], ids: Iterable[str]
) -> list[tuple[str, str]]:
    """
    Every message of the families and answers named, as (message ID, text).
    """
    wanted = set(ids)
    found = [
        (m.id, m.text) for f in families if f.family_id in wanted for m in f.messages
    ]
    found += [
        (f"{a.answer_id}/{language}", text)
        for a in answers
        if a.answer_id in wanted
        for language, text in a.texts.items()
    ]
    return found


def problems(
    families: Sequence[Family] | None = None, answers: Sequence[Answer] | None = None
) -> list[str]:
    families = load() if families is None else families
    answers = load_answers() if answers is None else answers
    found: list[str] = []
    ids = [f.family_id for f in families] + [a.answer_id for a in answers]
    found += [
        f"{i} appears twice" for i in sorted({i for i in ids if ids.count(i) > 1})
    ]
    for group in GROUPS:
        count = sum(f.group == group for f in families)
        if count != PER_GROUP:
            found.append(f"{group} has {count} families, not {PER_GROUP}")
    for kind in ANSWER_KINDS:
        count = sum(a.kind == kind for a in answers)
        if count != PER_ANSWER_KIND:
            found.append(f"{kind} has {count} answers, not {PER_ANSWER_KIND}")
    for family in families:
        found += _family_problems(family)
    for answer in answers:
        found += _answer_problems(answer)
    held = held_out_ids(families, answers)
    found += _too_close(
        texts(families, answers, held),
        texts(families, answers, set(ids) - held),
    )
    return found
