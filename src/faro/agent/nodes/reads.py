"""
The reads a card request may ask for: its status, its available credit, and its recent transactions.
"""

from typing import Any

from faro.agent.gateway import ToolCall
from faro.agent.nodes.calls import tool
from faro.agent.nodes.cards import card_facts, seen
from faro.agent.nodes.state import OFFERED, State, decided, offering, said, writable
from faro.agent.payload import offered, required
from faro.agent.scope import SCOPE


async def read(state: State) -> dict[str, Any]:
    """
    A read's answer follows what the turn already said, such as a confirmation a new request ended.
    """
    readers = {"card_status": read_status, "available_credit": read_credit}
    update = await readers.get(state["label"] or "", read_transactions)(state)
    if "say" in update:
        update["say"] = [*state.get("say", []), *update["say"]]
    return update


def failed_read(call: ToolCall) -> dict[str, Any] | None:
    """
    A read that didn't answer: denied is a refusal (POL-49), a customer not served in full the tool refused is handed off
    by the caller, and anything else can't be answered now (POL-48).
    """
    if call.outcome == "denied":
        return {"case": "refused"}
    if call.outcome == "refused":
        return {"case": "not_served"}
    if call.outcome != "ok" or call.result is None:
        return {"case": "unavailable"}
    return None


def not_served(state: State, label: str, calls: list[str]) -> dict[str, Any]:
    return {
        "case": "handoff",
        "queue": None,
        "handoff": required(
            "customer_not_active",
            label,
            ["POL-12"],
            calls=calls,
            cards=[c["card_id"] for c in state.get("targets", [])],
        ),
    }


def conflict_offer(
    state: State,
    label: str,
    rules: list[str],
    calls: list[str],
    cards: list[str],
    say: list[list[Any]],
) -> dict[str, Any]:
    """
    POL-31: a person is offered only when the customer asks which of two conflicting facts is right.
    """
    request = offered(
        "record_conflict", label, ["POL-31"], rules, calls=calls, cards=cards
    )
    return {
        "say": [*say, *said(("conflict_unresolved", {}), ("handoff_offer", {}))],
        "offer": offering(request, "answer"),
        "decision": decided("answer", rules, OFFERED, label),
    }


async def read_status(state: State) -> dict[str, Any]:
    """
    Each card meant, read with get_card for its expiration (POL-01, POL-14, POL-21). An Active card past its expiration
    is reported with both facts (POL-31).
    """
    scope, label = SCOPE.get(), "card_status"
    listed = state.get("listed")
    calls = [listed] if listed else []
    shown: list[dict[str, Any]] = []
    for card in state["targets"]:
        call = await tool(scope, "get_card", card_id=card["card_id"])
        calls.append(call.call_id)
        failed = failed_read(call)
        if failed is not None:
            return failed
        if call.result is None or "card" not in call.result:
            return {"case": "unavailable"}
        shown.append(call.result["card"])
    rules = ["POL-01", "POL-21"]
    every = (state.get("details") or {}).get("cards") == "all"
    listing: dict[str, Any] = {}
    if len(shown) == 1 and not every:
        say = writable("card_status", {"card": shown[0]}, seen(shown[0]))
    else:
        rules.append("POL-14")
        say = writable(
            "cards_status" if len(shown) > 1 else "only_card_status",
            {"statuses": shown},
            " ".join(seen(card) for card in shown),
        )
        listing = {"shown_cards": [c["card_id"] for c in shown]}
    conflicted = [
        c for c in shown if c["product_status"] == "Active" and c["past_expiration"]
    ]
    say += [part for c in conflicted for part in said(("past_expiration", {"card": c}))]
    if not conflicted:
        return {
            "say": say,
            "decision": decided("answer", rules, label=label),
            **listing,
        }
    rules += ["POL-30", "POL-31"]
    if (state.get("details") or {}).get("conflict") == "asks_which":
        return {
            **conflict_offer(
                state, label, rules, calls, [c["card_id"] for c in conflicted], say
            ),
            **listing,
        }
    return {"say": say, "decision": decided("answer", rules, label=label), **listing}


def credit_seen(figure: dict[str, Any]) -> str:
    over = figure["credit"]["availability"] == "over_limit"
    return seen(figure["card"]) + (
        " Its balance is over its limit, so it has no credit available."
        if over
        else " Its balance is within its limit."
    )


def figures(figured: list[dict[str, Any]], country: str, as_of: str) -> list[list[Any]]:
    """
    The cards with a figure, in one model call: one in a sentence, and two or more in a list under one (POL-14).
    """
    if not figured:
        return []
    facts = {"country": country, "as_of": as_of}
    if len(figured) == 1:
        (only,) = figured
        over = only["credit"]["availability"] == "over_limit"
        return writable(
            "credit_over_limit" if over else "credit_available",
            {**facts, **only},
            credit_seen(only),
        )
    return writable(
        "credits_available",
        {**facts, "credits": figured},
        " ".join(credit_seen(figure) for figure in figured),
    )


async def read_credit(state: State) -> dict[str, Any]:
    """
    Each card meant, read with get_available_credit, which computes the figure (POL-01, POL-18, POL-19, POL-22 to
    POL-24): an active credit card's credit, within or over its limit; a debit card or one that isn't active declined
    with the reason; a missing limit abstained on, with a person offered. An Active card past its expiration is
    reported with both facts (POL-31). The cards with a figure come first, then each without one, then each past its
    expiration.
    """
    scope, label = SCOPE.get(), "available_credit"
    listed = state.get("listed")
    calls = [listed] if listed else []
    figured: list[dict[str, Any]] = []
    others: list[list[Any]] = []
    notes: list[list[Any]] = []
    rules: list[str] = []
    missing: list[str] = []
    for card in state["targets"]:
        call = await tool(scope, "get_available_credit", card_id=card["card_id"])
        calls.append(call.call_id)
        failed = failed_read(call)
        if failed is not None and failed["case"] == "not_served":
            return not_served(state, label, calls)
        if failed is not None or call.result is None or "card" not in call.result:
            return failed or {"case": "unavailable"}
        credit = call.result["card"]
        availability = credit["availability"]
        if availability in ("available", "over_limit"):
            figured.append({"card": card, "credit": credit})
            over = availability == "over_limit"
            rules += ["POL-01", "POL-19", "POL-22", *(["POL-23"] if over else [])]
        elif availability == "no_limit":
            missing.append(card["card_id"])
            others += said(("credit_no_limit", {"card": card}))
            rules += ["POL-24"]
        else:
            name = (
                "credit_debit_card"
                if availability == "debit_card"
                else "credit_not_active"
            )
            others += said(
                (name, {"card": {**card, "product_status": credit["product_status"]}})
            )
            rules += ["POL-22"]
        if card["product_status"] == "Active" and card["past_expiration"]:
            notes += said(("past_expiration", {"card": card}))
            rules += ["POL-31"]
    if len(state["targets"]) > 1:
        rules.append("POL-14")
    rules = list(dict.fromkeys(rules))
    say = [
        *figures(figured, state.get("country", ""), scope.business_date),
        *others,
        *notes,
    ]
    outcome = "answer" if figured else "abstain" if missing else "decline"
    listing = (
        {"shown_cards": [f["card"]["card_id"] for f in figured]}
        if len(figured) > 1
        else {}
    )
    if not missing:
        return {"say": say, "decision": decided(outcome, rules, label=label), **listing}
    request = offered(
        "missing_data", label, ["POL-24"], rules, calls=calls, cards=missing
    )
    return {
        "say": [*say, *said(("handoff_offer", {}))],
        "offer": offering(request, outcome),
        "decision": decided(outcome, rules, OFFERED, label),
        **listing,
    }


async def read_transactions(state: State) -> dict[str, Any]:
    """
    A page of the card's transactions in the window, newest first, 10 at a time, or the page after the one shown last
    when the customer asks for the next 10, with the window's dates; a card with none, or none left, is answered so
    (POL-19, POL-25). The card and the cursor are kept for the next page.
    """
    scope, label = SCOPE.get(), "recent_transactions"
    target = state["target"]
    assert target is not None
    card = card_facts(target)
    paging = state.get("paging")
    following = (
        (state.get("details") or {}).get("page") == "next"
        and paging is not None
        and paging["card_id"] == target["card_id"]
    )
    rules = ["POL-19", "POL-25"]
    if following and paging is not None and paging["cursor"] is None:
        return {
            "say": said(
                ("transactions_no_more", {"card": card, "window": paging["window"]})
            ),
            "decision": decided("answer", rules, label=label),
        }
    call = await tool(
        scope,
        "find_transactions",
        card_id=target["card_id"],
        **({"cursor": paging["cursor"]} if following and paging else {}),
    )
    failed = failed_read(call)
    if failed is not None and failed["case"] == "not_served":
        return not_served(state, label, [target["listed"], call.call_id])
    if failed is not None or call.result is None or "transactions" not in call.result:
        return failed or {"case": "unavailable"}
    result = call.result
    kept = {
        "card_id": target["card_id"],
        "cursor": result["next_cursor"],
        "window": result["window"],
    }
    if not result["transactions"]:
        name = "transactions_no_more" if following else "transactions_none"
        return {
            "paging": kept,
            "say": said((name, {"card": card, "window": result["window"]})),
            "decision": decided("answer", rules, label=label),
        }
    facts = {
        "card": card,
        "page": result["transactions"],
        "window": result["window"],
        "country": state.get("country", ""),
    }
    say = writable(
        "transactions_next" if following else "transactions_page",
        facts,
        seen(card)
        + (
            " These are the transactions after the ones shown before."
            if following
            else ""
        ),
    )
    if result["next_cursor"] is not None:
        say += said(("transactions_more", {}))
    return {
        "paging": kept,
        "say": say,
        "decision": decided("answer", rules, label=label),
    }
