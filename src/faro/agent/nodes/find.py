"""
The transaction a charge or a decline is about, and the decline explained (POL-27 to POL-32, POL-39).
"""

from typing import Any

from faro.agent.language import OTHER
from faro.agent.models import ModelFailedError
from faro.agent.nodes.calls import tool
from faro.agent.nodes.cards import card_facts, listing, seen, shown, today
from faro.agent.nodes.state import (
    OFFERED,
    QUESTIONS,
    State,
    decided,
    heard,
    latest_text,
    offering,
    request_text,
    said,
    writable,
)
from faro.agent.payload import offered, required
from faro.agent.scope import SCOPE

# The window's pages a charge is looked for in, and the transactions listed for the customer to choose (POL-27).
PAGES = 3
SHOWN = 5
KINDS_OF_TRANSACTION = {
    "unrecognized_charge": "The customer reports a charge they don't recognize.",
    "decline_reason": "The customer asks why a transaction was declined.",
}


async def find_transaction(state: State) -> dict[str, Any]:
    """
    Looks for the transaction the customer means among the card's transactions in the 90-day window, in any status: a
    charge they don't recognize (POL-39, as in POL-27), or a decline (POL-27). The model reads them numbered and says
    which fit what the customer said: one is the transaction, several are listed, newest first and five at most, for the
    customer to choose, and none is said so; among several that fit a decline, the Declined ones are meant, if any. For
    a charge, a read that fails, or a choice not settled after two questions, is recorded in the handoff, and an Active
    card is offered the block next, while any other card's charge goes to dispute intake at once. A decline is explained
    next.
    """
    scope = SCOPE.get()
    asked = state.get("asking") or {}
    answering = asked.get("detail") == "transaction"
    target = asked["target"] if answering else state["target"]
    assert target is not None
    label: str = (
        asked["label"] if answering else state.get("label")
    ) or "unrecognized_charge"
    say = state.get("say", [])
    turn: dict[str, Any] = {"label": label, "case": "fixed", "asking": None}
    card = card_facts(target)
    country = state.get("country", "")
    declined = label == "decline_reason"
    failed = False
    if answering:
        turn["text"] = asked.get("text")
        calls, candidates = asked["calls"], asked["candidates"]
        context = (
            f"{KINDS_OF_TRANSACTION[label]}\n{today(scope.business_date)}\n"
            "The customer is answering which of these transactions they mean:\n"
            + listing(candidates)
        )
    else:
        calls, candidates, cursor = [], [], None
        for _ in range(PAGES):
            call = await tool(
                scope,
                "find_transactions",
                card_id=target["card_id"],
                **({} if cursor is None else {"cursor": cursor}),
            )
            calls.append(call.call_id)
            if call.outcome == "denied":
                return {**turn, "case": "refused"}
            if call.outcome != "ok" or call.result is None:
                failed = True
                break
            candidates += call.result["transactions"]
            cursor = call.result.get("next_cursor")
            if cursor is None:
                break
        context = (
            f"{KINDS_OF_TRANSACTION[label]}\n{today(scope.business_date)}\n"
            + listing(candidates)
        )
    fitting: list[dict[str, Any]] = []
    if candidates:
        text = latest_text(state) if answering else request_text(state)
        try:
            chosen = await scope.models.choose(text, context)
        except ModelFailedError:
            failed = True
        else:
            if chosen.language == OTHER:
                # POL-51: a question asked stays pending.
                return {**turn, "case": "third_language", "asking": state.get("asking")}
            turn |= heard(state, text, chosen.language)
            if answering and chosen.question == "unanswered":
                return {**turn, "case": "aside", "asking": asked}
            fitting = [
                candidates[n - 1]
                for n in dict.fromkeys(chosen.fitting)
                if 1 <= n <= len(candidates)
            ]
    if declined:
        # POL-27: among several that fit, the Declined ones are meant, if any.
        fitting = [
            t for t in fitting if t["transaction_status"] == "Declined"
        ] or fitting
    rules = ["POL-27"] if declined else ["POL-27", "POL-39"]
    before = asked.get("questions", 0) if answering else 0
    if declined and failed:
        return {**turn, "case": "unavailable"}
    if len(fitting) > 1 and before < QUESTIONS:
        listed = fitting[:SHOWN]
        which = [
            "which_decline" if declined else "which_charge",
            {
                "card": card,
                "transactions": [shown(t) for t in listed],
                "country": country,
            },
        ]
        decision = decided("clarify", rules, "transaction", label)
        return {
            **turn,
            "asking": {
                "detail": "transaction",
                "questions": before + 1,
                "label": label,
                "target": target,
                "candidates": listed,
                "calls": calls,
                "text": asked.get("text") or request_text(state),
                "asked": which,
                "decision": decision,
                "cited": {
                    "calls": [target["listed"], *calls],
                    "cards": [target["card_id"]],
                },
            },
            "say": [*say, which],
            "decision": decision,
        }
    if declined:
        return await explained(state, turn, target, fitting, [target["listed"], *calls])
    found = fitting[0] if len(fitting) == 1 else None
    if len(fitting) > 1:
        lead, rules = said(("clarification_stopped", {})), [*rules, "POL-17"]
    elif found is not None:
        lead = said(
            (
                "charge_found",
                {"card": card, "transaction": shown(found), "country": country},
            )
        )
    elif failed:
        lead = said(("charge_unread", {"card": card}))
    else:
        lead = said(("charge_not_found", {"card": card}))
    charge = {
        "calls": calls,
        "transactions": [] if found is None else [found["transaction_id"]],
    }
    if target["product_status"] == "Active":
        return {
            **turn,
            "case": "confirm",
            "say": [*say, *lead],
            "rules": rules,
            "target": {**target, "reason": "unrecognized_charge", "charge": charge},
        }
    name = (
        "already_blocked" if target["product_status"] == "Blocked" else "not_blockable"
    )
    return {
        **turn,
        "case": "handoff",
        "say": [*say, *lead, *said((name, {"card": card}))],
        "handoff": required(
            "unrecognized_charge",
            label,
            [*rules, "POL-34"],
            calls=[target["listed"], *calls],
            cards=[target["card_id"]],
            transactions=charge["transactions"],
        ),
    }


async def explained(
    state: State,
    turn: dict[str, Any],
    target: dict[str, Any],
    fitting: list[dict[str, Any]],
    calls: list[str],
) -> dict[str, Any]:
    """
    A decline found, explained (POL-02, POL-27 to POL-30, POL-32): a Declined one by its code's meaning and nothing
    else, or abstained on with a person offered when the code is missing or unlisted; a Pending, Reversed, or Approved
    one by its status. A conflict between records is stated, never resolved, and a person is offered when the customer
    asks which fact is right (POL-30, POL-31). Several that two questions didn't settle offer a person (POL-17).
    """
    scope = SCOPE.get()
    label, say = "decline_reason", state.get("say", [])
    country = state.get("country", "")
    card = card_facts(target)
    if len(fitting) > 1:
        stopped = ["POL-27", "POL-17"]
        request = offered(
            "clarification_failed",
            label,
            ["POL-17"],
            stopped,
            calls=calls,
            cards=[target["card_id"]],
        )
        return {
            **turn,
            "say": [*say, *said(("clarification_stopped", {}), ("handoff_offer", {}))],
            "offer": offering(request, "abstain"),
            "decision": decided("abstain", stopped, OFFERED, label),
        }
    if not fitting:
        return {
            **turn,
            "say": [*say, *said(("decline_not_found", {"card": card}))],
            "decision": decided("answer", ["POL-27"], label=label),
        }
    found = fitting[0]
    facts = {"card": card, "transaction": found, "country": country}
    rules = ["POL-27"]
    conflicts: list[list[Any]] = []
    if found["before_card_opening"]:
        conflicts += said(("before_opening", {"card": card}))
    if (
        found["transaction_status"] == "Declined"
        and found["response_code"] == "54"
        and not found["after_card_expiration"]
    ):
        call = await tool(scope, "get_card", card_id=target["card_id"])
        calls.append(call.call_id)
        read = (call.result or {}).get("card")
        if call.outcome == "ok" and read and read["expiration_date"] is not None:
            conflicts += said(("code_conflict", {"card": read}))
    if conflicts:
        rules += ["POL-30"]
    if found["transaction_status"] != "Declined":
        parts = writable("decline_status", facts, seen(card, found))
        rules += ["POL-28"]
    elif found["response_meaning"] is None:
        # POL-32: no reason on record, so the agent abstains on it.
        rules += ["POL-02", "POL-32"]
        request = offered(
            "missing_data",
            label,
            ["POL-32"],
            rules,
            calls=calls,
            cards=[target["card_id"]],
            transactions=[found["transaction_id"]],
        )
        return {
            **turn,
            "say": [
                *say,
                *said(("decline_no_code", facts)),
                *conflicts,
                *said(("handoff_offer", {})),
            ],
            "offer": offering(request, "abstain"),
            "decision": decided("abstain", rules, OFFERED, label),
        }
    else:
        parts = writable("decline_explained", facts, seen(card, found))
        rules += ["POL-02", "POL-29"]
    asks = (state.get("details") or {}).get("conflict") == "asks_which"
    if conflicts and asks:
        rules += ["POL-31"]
        request = offered(
            "record_conflict",
            label,
            ["POL-31"],
            rules,
            calls=calls,
            cards=[target["card_id"]],
            transactions=[found["transaction_id"]],
        )
        return {
            **turn,
            "say": [
                *say,
                *parts,
                *conflicts,
                *said(("conflict_unresolved", {}), ("handoff_offer", {})),
            ],
            "offer": offering(request, "answer"),
            "decision": decided("answer", rules, OFFERED, label),
        }
    return {
        **turn,
        "say": [*say, *parts, *conflicts],
        "decision": decided("answer", rules, label=label),
    }
