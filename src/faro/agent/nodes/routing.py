"""
Where a message's requests go: the router's labels in POL-05's order, the queue, a message that answers no question
asked (POL-06), and the requests the chat doesn't serve (POL-41 to POL-44).
"""

from typing import Any

from faro.agent.language import DEFAULT, OTHER
from faro.agent.models import Label, ModelFailedError
from faro.agent.nodes.calls import tool
from faro.agent.nodes.cards import fits, hints
from faro.agent.nodes.state import (
    CASES,
    OFFERED,
    State,
    decided,
    heard,
    latest_text,
    offering,
    paged,
    request_text,
    routing,
    said,
    small_talk,
)
from faro.agent.payload import offered, required
from faro.agent.scope import SCOPE
from faro.agent.texts import FIXED


async def begin(state: State) -> dict[str, Any]:
    """
    Opens the turn's state; the language stays until the model call that reads the message says otherwise (POL-50).
    """
    return {
        "language": state.get("language", DEFAULT),
        "label": None,
        "case": "fixed",
        "cards": [],
        "targets": [],
        "text": None,
        "parts": [],
        "names": [],
        "say": [],
        "decision": None,
        "rules": [],
        "details": None,
        "target": None,
        "block": None,
        "complaint": False,
        "listed": None,
        "handoff": None,
        "then": None,
        "offer": None,
    }


async def route(state: State) -> dict[str, Any]:
    scope = SCOPE.get()
    text = latest_text(state)
    try:
        routed = await scope.models.route(text, paged(state))
    except ModelFailedError:
        return {"case": "unavailable", "queue": None}
    if routed.language == OTHER:
        # POL-51, whatever labels came with it.
        return {"label": None, "case": "third_language", "queue": None}
    return {**heard(state, text, routed.language), **routing(routed, text)}


async def next_request(state: State) -> dict[str, Any]:
    """
    Serves the next queued request, from the message that asked for it, once the one before it ended (POL-05).
    """
    queue = state["queue"]
    assert queue is not None
    label, *rest = queue["labels"]
    return {
        "label": label,
        "case": CASES[label],
        "queue": {**queue, "labels": rest} if rest else None,
        "text": queue["text"],
        "complaint": queue.get("complaint", False),
        "say": [],
        "decision": None,
        "rules": [],
        "details": None,
        "target": None,
        "targets": [],
        "block": None,
        "listed": None,
        "handoff": None,
        "then": None,
    }


async def list_cards(state: State) -> dict[str, Any]:
    """
    Reads the customer's cards and whether they are served in full, before a request for a person or one the chat may
    not serve (POL-12). A customer who isn't is handed off without the status being named, in one case for every request
    the message holds, and so is one who asks for a person or complains (POL-44); both handoffs cite the cards read.
    """
    call = await tool(SCOPE.get(), "list_cards")
    if call.outcome == "denied":
        return {"case": "refused"}
    if call.outcome != "ok" or call.result is None:
        return {"case": "unavailable"}
    label = state.get("label")
    cards = [c["card_id"] for c in call.result["cards"]]
    if not call.result["customer"]["served_in_full"]:
        return {
            "case": "handoff",
            "queue": None,
            "handoff": required(
                "customer_not_active",
                label,
                ["POL-12"],
                calls=[call.call_id],
                cards=cards,
            ),
        }
    if label == "talk_to_human":
        return {
            "case": "handoff",
            "handoff": required(
                "complaint" if state.get("complaint") else "customer_request",
                label,
                ["POL-44"],
                calls=[call.call_id],
                cards=cards,
            ),
        }
    return {
        "case": "unsupported",
        "listed": call.call_id,
        "country": call.result["customer"]["country"],
        "cards": call.result["cards"],
    }


async def unsupported(state: State) -> dict[str, Any]:
    """
    What the chat doesn't serve (POL-41 to POL-43): an unblock is handed off, since it needs stronger proof of identity
    than a chat session gives; a replacement, a PIN, a limit increase, or another card service is declined with a person
    offered; anything outside cards is declined, with a person only if the customer asks for one. A request about
    someone else's card or account is refused without saying whether it exists (POL-08).
    """
    scope = SCOPE.get()
    label: Label = "unsupported"
    text = request_text(state)
    try:
        extracted = await scope.models.extract(
            text, "The customer asks for something the chat may not serve."
        )
    except ModelFailedError:
        return {"case": "unavailable"}
    if extracted.language == OTHER:
        return {"case": "third_language"}
    details = extracted.model_dump()
    if details.get("owner") == "someone_else":
        return {"case": "other_person"}
    service = details.get("service") or "other_card_service"
    listed = [c for c in (state.get("listed"),) if c is not None]
    if service == "unblock":
        card_type, last_four = hints(details)
        named = [c for c in state.get("cards", []) if fits(c, card_type, last_four)]
        meant = named if (card_type or last_four) and len(named) == 1 else []
        return {
            "case": "handoff",
            "say": [*state.get("say", []), *said(("unblock_by_person", {}))],
            "handoff": required(
                "unblock_request",
                label,
                ["POL-41"],
                calls=listed,
                cards=[c["card_id"] for c in meant],
            ),
        }
    if service == "outside_cards":
        return {
            "say": [*state.get("say", []), *said(("outside_cards", {}))],
            "decision": decided("decline", ["POL-43"], label),
        }
    request = offered(
        "unsupported_request", label, ["POL-42"], ["POL-42"], calls=listed
    )
    return {
        "say": [
            *state.get("say", []),
            *said(("unsupported_service", {"service": service}), ("handoff_offer", {})),
        ],
        "offer": offering(request, "decline"),
        "decision": decided("decline", ["POL-42"], label, OFFERED),
    }


async def aside(state: State) -> dict[str, Any]:
    """
    A message the step that asked read as no answer to its question, read as a new one (POL-06): one with no request
    gets its short reply and the question again, which counts toward no limit (POL-17), and a new request ends the
    question and is served, after the dispute handoff a charge's question owes (POL-39).
    """
    scope = SCOPE.get()
    asked = state["asking"]
    assert asked is not None
    text = latest_text(state)
    try:
        routed = await scope.models.route(text, paged(state))
    except ModelFailedError:
        return {"case": "unavailable", "asking": None}
    if routed.language == OTHER:
        return {"case": "third_language"}
    turn = heard(state, text, routed.language)
    if not routed.has_request:
        decision = asked["decision"]
        return {
            **turn,
            "case": "fixed",
            "say": [
                *said(
                    (asked_again(small_talk({**state, "no_request": routed.kind})), {})
                ),
                asked["asked"],
            ],
            "decision": {**decision, "rules": [*decision["rules"], "POL-06"]},
        }
    served = {
        **turn,
        **routing(routed, text),
        "asking": None,
        "say": [],
        "decision": None,
        "rules": [],
        "details": None,
        "target": None,
        "targets": [],
        "block": None,
        "listed": None,
        "handoff": None,
        "then": None,
    }
    if asked.get("label") != "unrecognized_charge":
        return served
    cites = asked["cited"]
    return {
        **served,
        "label": "unrecognized_charge",
        "case": "handoff",
        "handoff": required(
            "unrecognized_charge",
            "unrecognized_charge",
            ["POL-06", "POL-39"],
            calls=cites["calls"],
            cards=cites["cards"],
        ),
        "then": {
            k: served[k]
            for k in ("label", "case", "complaint", "text", "no_request", "queue")
        },
    }


def asked_again(name: str) -> str:
    """
    The small talk before a question asked again, without the closing question its fixed text ends in (POL-06).
    """
    return f"{name}_aside" if f"{name}_aside" in FIXED else name
