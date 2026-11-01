"""
The card a request is about, and the reason for a block (POL-13 to POL-17, POL-35).
"""

import re
from typing import Any

from faro.agent.formats import moment
from faro.agent.language import OTHER
from faro.agent.models import ModelFailedError
from faro.agent.nodes.calls import tool
from faro.agent.nodes.cards import TYPES, card_facts, fits, hints
from faro.agent.nodes.state import (
    OFFERED,
    QUESTIONS,
    READS,
    State,
    decided,
    heard,
    latest_text,
    offering,
    paged,
    request_text,
    said,
)
from faro.agent.payload import offered, required
from faro.agent.scope import SCOPE
from faro.tools.transactions import window

# Four digits on their own: not part of a longer number, and not an amount's whole part ("1177.00").
FOUR_DIGITS = re.compile(r"(?<![0-9])(?<![0-9][.,])[0-9]{4}(?![0-9])(?![.,][0-9])")
# POL-14: "all my cards" is answered for each for these; transactions and a decline are read per card.
ALL = ("card_status", "available_credit")
EARLIER = "Earlier in the conversation, the chat listed these cards, in this order:"
ASKS = {
    "block_card": "The customer is asking to block a card.",
    "unrecognized_charge": "The customer reports a charge they don't recognize on one of their cards.",
    "card_status": "The customer asks about the status of their cards, or of one of them.",
    "available_credit": "The customer asks about the credit available on a credit card.",
    "decline_reason": "The customer asks why a card, or a payment with it, was declined.",
}


def cards_named(cards: list[dict[str, Any]], ids: list[str]) -> str:
    """
    The cards the IDs name, in the IDs' order, as the chat listed them.
    """
    by_id = {c["card_id"]: c for c in cards}
    return "\n".join(
        f"- {'credit' if c['product_type'] == TYPES['credit'] else 'debit'} card ending in {c['last_four']}"
        for c in (by_id[i] for i in ids if i in by_id)
    )


def extraction_context(
    state: State, asking: dict[str, Any], cards: list[dict[str, Any]], label: str
) -> str:
    """
    What the extraction reads beside the message: the question it answers, if any, or the request with the cards the
    chat last listed, so a card named by its place in that list ("la segunda") is the one at that place (POL-13).
    """
    if asking.get("detail") == "card":
        listed = cards_named(cards, asking["candidates"])
        return f"The customer is answering which of these cards they mean:\n{listed}"
    if asking.get("detail") == "reason":
        return "The customer is answering why they want to block the card."
    if label == "recent_transactions":
        period = window({"as_of": SCOPE.get().as_of})
        given = (
            "The customer asks about the recent transactions of one of their cards. The chat reads the period from "
            f"{moment(period['from'])} to {moment(period['to'])}."
        )
        more = paged(state)
        given = given if more is None else f"{given} {more}"
    else:
        given = ASKS[label]
    listed = cards_named(cards, state.get("shown_cards") or [])
    return f"{given}\n{EARLIER}\n{listed}" if listed else given


def mentioned(text: str, cards: list[dict[str, Any]]) -> str | None:
    """
    The last four digits a message gives bare, as in "la 4821", when exactly one of them ends a card of the customer's.
    """
    endings = {c["last_four"] for c in cards}
    found = {m for m in FOUR_DIGITS.findall(text) if m in endings}
    return found.pop() if len(found) == 1 else None


def applies(label: str, card: dict[str, Any]) -> bool:
    """
    The cards a request applies to, which POL-13 prefers among several that fit: active cards for a block, and for the
    charge whose block is offered, credit cards for available credit, and any card otherwise.
    """
    if label in ("block_card", "unrecognized_charge"):
        return bool(card["product_status"] == "Active")
    if label == "available_credit":
        return bool(card["product_type"] == TYPES["credit"])
    return True


def match(
    cards: list[dict[str, Any]],
    card_type: str | None,
    last_four: str | None,
    label: str = "block_card",
) -> tuple[str, list[dict[str, Any]]]:
    """
    The card a request means (POL-13 to POL-16): the customer's cards that fit the hints, and among several, the ones
    the request applies to, if any.
    """
    if not cards:
        return "no_cards", []
    fitting = [c for c in cards if fits(c, card_type, last_four)]
    if not fitting:
        return "no_match", cards
    meant = [c for c in fitting if applies(label, c)] or fitting
    if len(meant) == 1:
        return "settled", meant
    if last_four is not None:
        if len({c["product_type"] for c in meant}) > 1:
            return "which_type", meant
        return "ambiguous", meant
    return "which_card", meant


def question(
    state: State,
    asked: dict[str, Any] | None,
    detail: str,
    name: str,
    facts: dict[str, Any],
    rules: list[str],
    cites: tuple[str | None, list[str]],
    **held: Any,
) -> dict[str, Any]:
    """
    Asks for a detail, or stops once two questions haven't settled it and offers a handoff (POL-17), citing the listing
    and the cards the questions were about (cites). For a charge the customer doesn't recognize, a card that can't be
    settled goes to dispute intake at once instead, since that is the request's only handoff (POL-39).
    """
    asked = asked or {}
    before = asked.get("questions", 0) if asked.get("detail") == detail else 0
    label = held.get("label") or "block_card"
    if before >= QUESTIONS:
        listed, cards = cites
        stopped = [*rules, "POL-17"]
        say = [*state.get("say", []), *said(("clarification_stopped", {}))]
        if label == "unrecognized_charge":
            return {
                "case": "handoff",
                "asking": None,
                "say": say,
                "handoff": required(
                    "unrecognized_charge",
                    label,
                    [*stopped, "POL-39"],
                    calls=[listed] if listed else [],
                    cards=cards,
                ),
            }
        request = offered(
            "clarification_failed",
            label,
            ["POL-17"],
            stopped,
            calls=[listed] if listed else [],
            cards=cards,
        )
        return {
            "case": "fixed",
            "asking": None,
            "say": [*say, *said(("handoff_offer", {}))],
            "offer": offering(request, "abstain"),
            "decision": decided("abstain", stopped, OFFERED, label),
        }
    listed, cards = cites
    decision = decided(
        "clarify", rules, "card" if detail == "card" else "reason", label
    )
    return {
        "case": "fixed",
        "asking": {
            "detail": detail,
            "questions": before + 1,
            "asked": [name, facts],
            "decision": decision,
            "cited": {"calls": [listed] if listed else [], "cards": cards},
            **held,
        },
        "say": [*state.get("say", []), *said((name, facts))],
        "decision": decision,
    }


EMPTY = {
    "card_type": None,
    "last_four": None,
    "block_reason": None,
    "cards": None,
    "page": None,
    "owner": None,
    "conflict": None,
    "service": None,
    "question": None,
}


async def resolve_card(state: State) -> dict[str, Any]:
    """
    The card a request is about (POL-13 to POL-17). For a charge or a read, the customer's status comes first (POL-12),
    and a customer not served in full is handed off once for everything the message asked. A read about someone else's
    card is refused (POL-08), a request for transactions before the window is declined stating it (POL-25), "all my
    cards" is answered for each where POL-14 says so, and the next page is the card paged last unless another is named.
    For a charge, no reason is asked, the card is settled whatever its status, and what can't be settled or read goes
    into the charge's handoff rather than one of its own (POL-39).
    """
    scope = SCOPE.get()
    asked = state.get("asking") or {}
    label = asked.get("label") or state.get("label") or "block_card"
    charge = label == "unrecognized_charge"
    reading = label in READS
    turn: dict[str, Any] = {"label": label, "case": "fixed"}
    say = state.get("say", [])
    call = await tool(scope, "list_cards")
    if call.outcome == "denied":
        return {**turn, "case": "refused", "asking": None}
    if call.outcome != "ok" or call.result is None:
        if not charge:
            return {**turn, "case": "unavailable", "asking": None}
        return {
            **turn,
            "case": "handoff",
            "asking": None,
            "say": [*say, *said(("records_unavailable", {}))],
            "handoff": required(
                "unrecognized_charge", label, ["POL-39", "POL-48"], calls=[call.call_id]
            ),
        }
    turn["listed"] = call.call_id
    turn["country"] = country = call.result["customer"]["country"]
    cards: list[dict[str, Any]] = call.result["cards"]
    ids = [c["card_id"] for c in cards]
    if (charge or reading) and not call.result["customer"]["served_in_full"]:
        return {
            **turn,
            "case": "handoff",
            "asking": None,
            "queue": None,
            "handoff": required(
                "customer_not_active",
                label,
                ["POL-12"],
                calls=[call.call_id],
                cards=ids,
            ),
        }
    details = state.get("details")
    if details is None:
        text = latest_text(state) if asked else request_text(state)
        try:
            extracted = await scope.models.extract(
                text, extraction_context(state, asked, cards, label)
            )
        except ModelFailedError:
            if not charge:
                return {**turn, "case": "unavailable", "asking": None}
            # A charge's card can still be asked for.
            details = dict(EMPTY)
        else:
            if extracted.language == OTHER:
                # POL-51: a question asked stays pending.
                return {**turn, "case": "third_language"}
            turn |= heard(state, text, extracted.language)
            details = extracted.details()
            # A reason that came back answers the question, whatever the mark says (POL-06, POL-35).
            answered = (
                asked.get("detail") == "reason" and details["block_reason"] is not None
            )
            if asked and details["question"] == "unanswered" and not answered:
                return {**turn, "case": "aside"}
    # A charge someone else made on the customer's card is still theirs to report (POL-39).
    if details.get("owner") == "someone_else" and not charge:
        return {**turn, "case": "other_person", "asking": None}
    if label == "recent_transactions" and details.get("page") == "earlier":
        return {
            **turn,
            "asking": None,
            "say": [
                *say,
                *said(
                    (
                        "transactions_earlier",
                        {"window": window({"as_of": scope.as_of})},
                    )
                ),
            ],
            "decision": decided("decline", ["POL-25"], label=label),
        }
    card_type, last_four = hints(details)
    if last_four is None and asked.get("detail") != "reason":
        last_four = mentioned(
            latest_text(state) if asked else request_text(state), cards
        )
    reason = (
        "unrecognized_charge"
        if charge
        else details.get("block_reason") or asked.get("reason")
    )
    paging = state.get("paging")
    if (
        label == "recent_transactions"
        and details.get("page") == "next"
        and paging is not None
        and card_type is None
        and last_four is None
    ):
        asked = {**asked, "card_id": paging["card_id"]}
    card = next((c for c in cards if c["card_id"] == asked.get("card_id")), None)
    if card is None or not fits(card, card_type, last_four):
        card_type = card_type or asked.get("card_type")
        last_four = last_four or asked.get("last_four")
        if (
            label in ALL
            and details.get("cards") == "all"
            and card_type is None
            and last_four is None
            and cards
        ):
            meant = [c for c in cards if applies(label, c)] or cards
            return {
                **turn,
                "case": "reading",
                "asking": None,
                "details": details,
                "targets": meant,
                "target": None,
                "recent": None,
                "text": asked.get("text") or state.get("text"),
            }
        # POL-13: a message that says nothing about which card means the one last settled on.
        remembered = (
            next((c for c in cards if c["card_id"] == state.get("recent")), None)
            if not asked and card_type is None and last_four is None
            else None
        )
        found, meant = (
            ("settled", [remembered])
            if remembered is not None
            else match(cards, card_type, last_four, label)
        )
        if found == "no_cards":
            if charge:
                return {
                    **turn,
                    "case": "handoff",
                    "asking": None,
                    "say": [*say, *said(("no_cards", {}))],
                    "handoff": required(
                        "unrecognized_charge",
                        label,
                        ["POL-13", "POL-39"],
                        calls=[call.call_id],
                    ),
                }
            return {
                **turn,
                "asking": None,
                "say": [*say, *said(("no_cards", {}))],
                "decision": decided(
                    "answer" if label == "card_status" else "decline",
                    ["POL-13", "POL-16"],
                    label=label,
                ),
            }
        if found == "ambiguous":
            return {
                **turn,
                "case": "handoff",
                "asking": None,
                "say": [*say, *said(("ambiguous_card", {"last_four": last_four}))],
                "handoff": required(
                    "unrecognized_charge" if charge else "ambiguous_card",
                    label,
                    ["POL-13", "POL-15", "POL-39"] if charge else ["POL-13", "POL-15"],
                    calls=[call.call_id],
                    cards=[c["card_id"] for c in meant],
                    reported=reason,
                ),
            }
        if found != "settled":
            name, rules = {
                "no_match": ("no_matching_card", ["POL-13", "POL-16"]),
                "which_type": ("which_type", ["POL-13", "POL-15"]),
                "which_card": ("which_card", ["POL-13", "POL-14"]),
            }[found]
            if charge and found != "which_type":
                name = f"{name}_charge"
            elif reading and found != "which_type":
                name = f"{name}_read"
            facts = {"cards": [card_facts(c) for c in meant], "last_four": last_four}
            # Every question but the type's lists the cards.
            if found != "which_type":
                turn["shown_cards"] = [c["card_id"] for c in meant]
            return {
                **turn,
                **question(
                    state,
                    asked,
                    "card",
                    name,
                    facts,
                    rules,
                    (call.call_id, [c["card_id"] for c in meant]),
                    candidates=[c["card_id"] for c in meant],
                    card_type=card_type if found == "which_card" else None,
                    last_four=last_four if found != "no_match" else None,
                    reason=reason,
                    label=label,
                    text=asked.get("text") or request_text(state),
                ),
            }
        card = meant[0]
    turn["recent"] = card["card_id"]
    target = {
        **card,
        "served_in_full": call.result["customer"]["served_in_full"],
        "listed": call.call_id,
    }
    if charge:
        return {
            **turn,
            "case": "find",
            "asking": None,
            "target": target,
            "text": asked.get("text") or state.get("text"),
        }
    if reading:
        return {
            **turn,
            "case": "find" if label == "decline_reason" else "reading",
            "asking": None,
            "details": details,
            "target": target,
            "targets": [target],
            "country": country,
            "text": asked.get("text") or state.get("text"),
        }
    if card["product_status"] != "Active":
        name = (
            "already_blocked"
            if card["product_status"] == "Blocked"
            else "not_blockable"
        )
        return {
            **turn,
            "asking": None,
            "say": [*say, *said((name, {"card": card_facts(card)}))],
            "decision": decided("decline", ["POL-13", "POL-34"]),
        }
    if reason is None:
        return {**turn, "case": "ask_reason", "target": target}
    return {**turn, "case": "confirm", "target": {**target, "reason": reason}}


async def ask_reason(state: State) -> dict[str, Any]:
    target = state["target"]
    assert target is not None
    return question(
        state,
        state.get("asking"),
        "reason",
        "ask_reason",
        {"card": card_facts(target)},
        ["POL-13", "POL-35"],
        (state.get("listed"), [target["card_id"]]),
        card_id=target["card_id"],
    )
