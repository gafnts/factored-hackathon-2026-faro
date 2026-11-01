"""
The graph's state, and what every flow reads from it or builds into its updates.
"""

import uuid
from collections.abc import Mapping
from typing import Annotated, Final, Literal, NotRequired, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph.message import add_messages

from faro.agent.language import DEFAULT, Spoken, settled
from faro.agent.models import (
    ORDER,
    BlockReason,
    Details,
    Label,
    Language,
    NoRequest,
    RouterOutput,
)
from faro.agent.nodes.cards import (
    Card,
    CardFacts,
    ProductStatus,
    ProductType,
    Transaction,
    Window,
)
from faro.agent.payload import HandoffRequest
from faro.agent.records import Cited
from faro.agent.scope import SCOPE

# POL-17: after two questions that don't settle the same detail, stop asking.
QUESTIONS = 2
# A lost or stolen card left unblocked is handed to a person (POL-38).
MISSING = ("lost", "stolen")
CONTROL: Final = "confirm_control"
OFFERED: Final = "handoff_control"
# The tool calls a handoff may cite, the thread's latest first to go; a payload holds 60 at most.
CITED = 40
READS = ("card_status", "available_credit", "recent_transactions", "decline_reason")
# Where a request goes next, and the cases that end it with a fixed reply.
Step = Literal[
    "block",
    "read",
    "status",
    "find",
    "reading",
    "confirm",
    "ask_reason",
    "aside",
    "handoff",
    "unsupported",
    "blocking",
    "again",
    "fixed",
    "pointer",
    "not_served",
]
Ending = Literal[
    "no_request", "unavailable", "refused", "other_person", "third_language"
]
Case = Step | Ending
Outcome = Literal["answer", "clarify", "abstain", "decline", "block", "hand_off"]
Awaiting = Literal[
    "none", "card", "reason", "transaction", "confirm_control", "handoff_control"
]
# Where each request starts: every request but a block reads list_cards first (POL-12).
CASES: dict[Label, Step] = {
    "block_card": "block",
    "unrecognized_charge": "block",
    "card_status": "read",
    "available_credit": "read",
    "recent_transactions": "read",
    "decline_reason": "read",
    "talk_to_human": "status",
    "unsupported": "status",
}
WRITE = "written"
MORE = "The chat's last reply listed a page of a card's recent transactions and said the customer can ask for the next 10."


class Charge(TypedDict):
    """
    The reads that looked for a charge the customer doesn't recognize, and the transaction found, if any (POL-39).
    """

    calls: list[str]
    transactions: list[str]


class Target(Card):
    """
    The card a request settled on, with the customer's status and the listing that read it; a block adds its reason,
    and a charge's block the reads that looked for the charge.
    """

    served_in_full: bool
    listed: str
    reason: NotRequired[BlockReason]
    charge: NotRequired[Charge]


class Decision(TypedDict):
    """
    A request's decision, as the turn's record writes it (execution-record.schema.json, decision_entry).
    """

    request_label: Label | None
    outcome_class: Outcome
    awaiting: Awaiting
    rules: list[str]


class Offer(TypedDict):
    """
    An offered handoff, as the thread holds it until the handoff control answers (offering()).
    """

    offer_id: str
    reason_code: str
    request: HandoffRequest
    origin_jti: str
    outcome_class: Outcome


# A part of the reply: a fixed text's name and the facts it states, or WRITE and what the model writes from.
Facts = Mapping[str, object]
Said = tuple[str, Facts]


class Written(TypedDict):
    template: str
    facts: Facts
    shown: str


class Cites(TypedDict):
    """
    What a handoff cites about a question that stopped (the listing or the reads it asked from, and its cards), or
    about a pending confirmation (cited()), which adds the charge found.
    """

    calls: list[str]
    cards: list[str]
    transactions: NotRequired[list[str]]


class Asking(TypedDict, total=False):
    """
    The question the chat asked, which the next message may answer (POL-06, POL-17): which card (candidates are card
    IDs, with the hints and the reason the request gave), the reason for a block (the card's ID), or which transaction
    (candidates are the transactions listed, with the card and the reads). The next message reads it with a fallback
    to no question, hence every key is optional.
    """

    detail: Literal["card", "reason", "transaction"]
    questions: int
    asked: Said
    decision: Decision
    cited: Cites
    label: Label
    text: str
    card_id: str
    card_type: ProductType | None
    last_four: str | None
    reason: BlockReason | None
    candidates: list[str] | list[Transaction]
    target: Target
    calls: list[str]


class Pending(TypedDict):
    """
    A block waiting on the confirm control (POL-36): its confirmation, the card, how many messages typed while it
    shows were pointed to it, what a handoff about it would cite, and the draft that handoff would file.
    """

    confirmation_id: str
    card: CardFacts
    card_id: str
    reason: BlockReason
    expires_at: int
    origin_jti: str
    typed: int
    listed: str | None
    label: Label
    charge: Charge | None
    handoff_id: str | None
    confirmed_at: NotRequired[str]


class BlockOutput(TypedDict, total=False):
    """
    The fields of block_card's outputs the graph reads: the block's outcome, or the refusal and the card's status.
    """

    attempts: int
    block_outcome: Literal["verified", "not_verified"]
    refusal: str
    product_status: ProductStatus


class Block(TypedDict):
    call_id: str
    outcome: str
    result: BlockOutput | None
    planned: bool


class Queue(TypedDict):
    """
    The requests a message asked for that wait for the one being served, in POL-05's order.
    """

    labels: list[Label]
    text: str
    complaint: bool


class Paging(TypedDict):
    """
    The card whose transactions the chat listed last, and the cursor of the page after, null on the last (POL-25).
    """

    card_id: str
    cursor: str | None
    window: Window


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def kept(held: list[Cited], new: list[Cited]) -> list[Cited]:
    return [*held, *new][-CITED:]


class Private(TypedDict, total=False):
    """
    What a turn keeps for its own steps, which never reaches the chat (ADR-0004, What the chat receives).
    """

    language: Spoken
    read: int
    label: Label | None
    case: Case
    cards: list[Card]
    say: list[Said]
    decision: Decision | None
    rules: list[str]
    details: Details | None
    asking: Asking | None
    target: Target | None
    pending: Pending | None
    block: Block | None
    complaint: bool
    listed: str | None
    handoff: HandoffRequest | None
    then: "Update | None"
    offer: Offer | None
    evidence: Annotated[list[Cited], kept]
    country: str
    targets: list[Card]
    queue: Queue | None
    text: str | None
    paging: Paging | None
    recent: str | None
    # The cards the chat last listed, in their order, so a later message can name one by its place (POL-13).
    shown_cards: list[str] | None
    parts: list[str]
    names: list[str]
    no_request: NoRequest | None


class State(ChatState, Private):
    """
    The graph's state: the conversation, and what a turn keeps for its own steps.
    """


class Update(Private, total=False):
    """
    What a node returns: any of the state's keys, and the messages it adds.
    """

    messages: list[AnyMessage]


def latest_text(state: State) -> str:
    message = next(
        m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)
    )
    return message.text


def request_text(state: State) -> str:
    """
    The message the request being served came from: a queued request's, or the latest.
    """
    return state.get("text") or latest_text(state)


def routing(routed: RouterOutput, text: str) -> Update:
    """
    The first request in POL-05's order, and the rest queued with the message that asked for them; a message with no
    request, like any new one, leaves nothing queued (POL-05, POL-06).
    """
    if not routed.has_request:
        return {
            "label": None,
            "case": "no_request",
            "queue": None,
            "no_request": routed.kind,
        }
    first, *rest = [label for label in ORDER if label in routed.requests] or [
        "unsupported"
    ]
    return {
        "label": first,
        "case": CASES[first],
        "complaint": routed.complaint,
        "text": text,
        "no_request": None,
        "queue": (
            {"labels": rest, "text": text, "complaint": routed.complaint}
            if rest
            else None
        ),
    }


def heard(state: State, text: str, found: Language) -> Update:
    """
    The conversation's language after a model call read text as found (POL-50). Only the latest message sets it, so a
    queued request served later can't move it back, and only its first reading does, so a later call that reads the
    same message differently can't move it either (D-008); read counts the customer's messages read so far.
    """
    if text != latest_text(state):
        return {}
    count = sum(isinstance(m, HumanMessage) for m in state["messages"])
    if state.get("read") == count:
        return {}
    return {"language": settled(state.get("language", DEFAULT), found), "read": count}


def queued(state: State) -> list[Label]:
    queue = state.get("queue")
    return [] if queue is None else list(queue["labels"])


def paged(state: State) -> str | None:
    paging = state.get("paging")
    return MORE if paging is not None and paging.get("cursor") else None


def said(*parts: Said) -> list[Said]:
    return list(parts)


def decided(
    outcome_class: Outcome,
    rules: list[str],
    label: Label | None,
    awaiting: Awaiting = "none",
) -> Decision:
    return {
        "request_label": label,
        "outcome_class": outcome_class,
        "awaiting": awaiting,
        "rules": rules,
    }


def writable(template: str, facts: Facts, words: str) -> list[Said]:
    """
    A read's answer, which the model writes when its request is among WRITES, from the placeholders its fixed reply
    states and the words that say what the records show, never a figure (ADR-0004's amendment of 2026-10-01).
    """
    return [(WRITE, {"template": template, "facts": facts, "shown": words})]


def offering(request: HandoffRequest, outcome_class: Outcome) -> Offer:
    """
    An offered handoff, as the thread holds it until the handoff control answers: the handoff it would file, the
    sign-in that saw it, since an offer lapses with the session, and the outcome of the turn that made it, which a turn
    that leaves it pending repeats (POL-09, POL-45).
    """
    return {
        "offer_id": str(uuid.uuid4()),
        "reason_code": request["reason_code"],
        "request": request,
        "origin_jti": SCOPE.get().claims.origin_jti,
        "outcome_class": outcome_class,
    }


def cited(pending: Pending, *calls: str) -> Cites:
    """
    What a handoff about the pending confirmation cites: the listing that settled its card, a charge's window reads
    and the charge found, if any, and the calls named.
    """
    charge = pending.get("charge")
    return {
        "calls": [
            *([pending["listed"]] if pending["listed"] else []),
            *(charge["calls"] if charge else []),
            *calls,
        ],
        "cards": [pending["card_id"]],
        "transactions": charge["transactions"] if charge else [],
    }


def small_talk(state: State) -> str:
    """
    The fixed text a message with no request gets, from what the router read it as (POL-06): the introduction the
    first time the chat speaks and whenever the message is aimed at the chat itself, a bare injection included, and
    after that a line for a greeting, for thanks, or for a goodbye, and the capabilities for anything else.
    """
    no_request = state.get("no_request") or "other"
    if no_request in ("thanks", "closing"):
        return no_request
    introduced = any(isinstance(m, AIMessage) for m in state["messages"])
    if no_request == "about" or not introduced:
        return "no_request"
    return "greeting" if no_request == "greeting" else "capabilities"
