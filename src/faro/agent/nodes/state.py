"""
The graph's state, and what every flow reads from it or builds into its updates.
"""

import uuid
from typing import Annotated, Any, NotRequired, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph.message import add_messages

from faro.agent.language import DEFAULT, settled
from faro.agent.models import ORDER, BlockReason, RouterOutput
from faro.agent.nodes.cards import Card
from faro.agent.scope import SCOPE

# POL-17: after two questions that don't settle the same detail, stop asking.
QUESTIONS = 2
# A lost or stolen card left unblocked is handed to a person (POL-38).
MISSING = ("lost", "stolen")
CONTROL = "confirm_control"
OFFERED = "handoff_control"
# The tool calls a handoff may cite, the thread's latest first to go; a payload holds 60 at most.
CITED = 40
READS = ("card_status", "available_credit", "recent_transactions", "decline_reason")
# Where each request starts: every request but a block reads list_cards first (POL-12).
CASES = {
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


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def kept(held: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [*held, *new][-CITED:]


class State(ChatState, total=False):
    language: str
    read: int
    label: str | None
    case: str
    cards: list[Card]
    say: list[list[Any]]
    decision: dict[str, Any] | None
    rules: list[str]
    details: dict[str, Any] | None
    asking: dict[str, Any] | None
    target: Target | None
    pending: dict[str, Any] | None
    block: dict[str, Any] | None
    complaint: bool
    listed: str | None
    handoff: dict[str, Any] | None
    then: dict[str, Any] | None
    offer: dict[str, Any] | None
    evidence: Annotated[list[dict[str, Any]], kept]
    country: str
    targets: list[Card]
    queue: dict[str, Any] | None
    text: str | None
    paging: dict[str, Any] | None
    recent: str | None
    # The cards the chat last listed, in their order, so a later message can name one by its place (POL-13).
    shown_cards: list[str] | None
    parts: list[str]
    names: list[str]
    no_request: str | None


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


def routing(routed: RouterOutput, text: str) -> dict[str, Any]:
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


def heard(state: State, text: str, found: str) -> dict[str, Any]:
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


def queued(state: State) -> list[str]:
    return list((state.get("queue") or {}).get("labels", []))


def paged(state: State) -> str | None:
    paging = state.get("paging")
    return MORE if paging is not None and paging.get("cursor") else None


def said(*parts: tuple[str, dict[str, Any]]) -> list[list[Any]]:
    return [[name, facts] for name, facts in parts]


def decided(
    outcome_class: str,
    rules: list[str],
    awaiting: str = "none",
    label: str = "block_card",
) -> dict[str, Any]:
    return {
        "request_label": label,
        "outcome_class": outcome_class,
        "awaiting": awaiting,
        "rules": rules,
    }


def concluded(
    label: str | None, outcome_class: str, rules: list[str]
) -> dict[str, Any]:
    return {
        "request_label": label,
        "outcome_class": outcome_class,
        "awaiting": "none",
        "rules": rules,
    }


def writable(template: str, facts: dict[str, Any], words: str) -> list[list[Any]]:
    """
    A read's answer, which the model writes when its request is among WRITES, from the placeholders its fixed reply
    states and the words that say what the records show, never a figure (ADR-0004's amendment of 2026-10-01).
    """
    return [[WRITE, {"template": template, "facts": facts, "shown": words}]]


def offering(request: dict[str, Any], outcome_class: str) -> dict[str, Any]:
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


def cited(pending: dict[str, Any], *calls: str) -> dict[str, Any]:
    """
    What a handoff about the pending confirmation cites: the listing that settled its card, a charge's window reads
    and the charge found, if any, and the calls named.
    """
    charge = pending.get("charge") or {}
    return {
        "calls": [
            *([pending["listed"]] if pending.get("listed") else []),
            *charge.get("calls", []),
            *calls,
        ],
        "cards": [pending["card_id"]],
        "transactions": charge.get("transactions", []),
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
