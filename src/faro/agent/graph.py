"""
The graph (ADR-0004, The graph, and its amendment of 2026-10-01). A new message is routed, and its requests are served
in POL-05's order, each after the one before it ends: a block goes through resolve_card, ask_reason, and confirm to the
confirm control; a charge the customer doesn't recognize goes through resolve_card and find_transaction to the same
control when the card is Active, and to dispute intake however that ends (POL-39); a read goes through resolve_card,
which reads list_cards first, since the customer's status comes before the request (POL-12), then to read, or to
find_transaction for a decline; and a request for a person or one the chat may not serve reads list_cards, then is
handed off, declined, or offered a person (POL-41 to POL-44). A request that ends on a question or a control keeps the
rest queued until it ends (POL-05). An answer to the agent's own question goes back to the step that asked, and a pending
control's answer, or a message typed while it shows, resumes await_control (POL-06). The model call that reads a
message says which language it is mostly in, and code applies POL-50 and POL-51 to it: a third language gets POL-51's
reply, whatever labels came with it. Code decides every step: the model labels the request, extracts what the step allows, writes the reads' answers
with placeholders that code fills and checks, and writes a handoff's free text; every other reply is fixed text code
chooses (decision 8). Only the control confirms a block, and block_card acts only under the confirmation it confirmed
(POL-36). A handoff the policy requires is filed by the handoff node, from the evidence the thread's tool calls left in
state (POL-45 to POL-47). One it offers shows the handoff control after the reply, alone or beside a pending
confirmation, and is filed only when the control accepts it; while it shows, await_control takes the answers as it does
a confirmation's.

The public state is the conversation alone: the graph's input and output schemas hold messages only, and what a turn
keeps for its own steps never reaches the chat (What the chat receives). Each request's decision is handed to the turn's
record, and the entrypoint writes them after the controls' interrupt, when there is one; the turn's reply is one
message, one part per request it served.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.callbacks import adispatch_custom_event
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from faro.agent.check import failures
from faro.agent.confirmations import new_record
from faro.agent.filing import new_call_id
from faro.agent.gateway import ToolCall
from faro.agent.language import DEFAULT, OTHER, settled
from faro.agent.models import ModelFailedError, RouterOutput
from faro.agent.nodes.calls import bound, citing, tool
from faro.agent.nodes.cards import (
    KINDS,
    card_facts,
    fits,
    hints,
    listing,
    seen,
    shown,
    today,
)
from faro.agent.nodes.resolve import ask_reason, resolve_card
from faro.agent.nodes.routing import (
    aside,
    begin,
    list_cards,
    next_request,
    route,
    unsupported,
)
from faro.agent.nodes.state import (
    CONTROL,
    MISSING,
    OFFERED,
    QUESTIONS,
    READS,
    WRITE,
    ChatState,
    State,
    cited,
    concluded,
    decided,
    heard,
    latest_text,
    offering,
    paged,
    queued,
    request_text,
    routing,
    said,
    small_talk,
    writable,
)
from faro.agent.payload import (
    built,
    context,
    done,
    offered,
    required,
    transcript,
    written,
)
from faro.agent.records import wall_time
from faro.agent.scope import SCOPE, Scope
from faro.agent.texts import FIXED, LANGUAGE_NAMES, fill, placeholders, render, values
from faro.policy import POLICY_VERSION
from faro.policy.handoffs import HANDOFFS

# The wrapper turns this event into one TEXT_MESSAGE_START, CONTENT, and END, so a reply reaches the chat whole.
EMIT_MESSAGE = "manually_emit_message"
# The blocks whose confirmation's end may require a handoff, drafted when the control shows (POL-38, POL-39).
DRAFTED = (*MISSING, "unrecognized_charge")
# file_handoff reads at most this many of the sign-in's turns.
TURNS = 20
# The window's pages a charge is looked for in, and the transactions listed for the customer to choose (POL-27).
PAGES = 3
SHOWN = 5
# The reads whose answer the model writes; any other is its fixed reply (ADR-0004's amendment of 2026-10-01).
WRITES = frozenset(READS)
# What each placeholder holds, as the reply's model reads it; never its value.
DESCRIPTIONS = {
    "card": "the card, by its type and last four digits",
    "card.status": "the card's status",
    "card.expiration": "the card's expiration month and year, or that none is recorded",
    "cards": "the customer's cards, one per line, each with its status and expiration; put it on a line of its own",
    "credit.available": "the credit available on the card, with its currency",
    "credit.over_by": "the amount by which the card's balance exceeds its limit, with its currency",
    "credits": (
        "the credit available on every card the answer covers, a line per card, all in this one placeholder; write it"
        " once, on a line of its own"
    ),
    "as_of": "the date the figures are as of, stated as the records' date (Spanish 'al', Portuguese 'em'), never as a start",
    "window.from": "when the period of transactions shown starts",
    "window.to": "when the period of transactions shown ends",
    "transactions": "the transactions, one per line, newest first; put it on a line of its own",
    "transaction": "the transaction found: its date, its merchant or its type, and its amount; put it on a line of its own",
    "transaction.status": "the transaction's status",
    "transaction.meaning": "the reason on record for the decline; put it on the line right under the transaction",
}
# The shape of each answer the model writes, so its sentences carry the placeholders instead of listing them.
SHAPES = {
    "card_status": "One sentence that names the card and its status, then its expiration.",
    "cards_status": "One sentence that introduces the customer's cards, then their list on a line of its own.",
    "only_card_status": "One sentence that introduces the customer's only card, then it on a line of its own.",
    "credit_available": "One sentence: as of the date, the card has that credit available.",
    "credits_available": (
        "One sentence that says, as of the date, this is the credit available on the customer's cards, then their list"
        " on a line of its own, and nothing after it."
    ),
    "credit_over_limit": (
        "One sentence: as of the date, the card has no credit available, since its balance exceeds its limit by"
        " that amount."
    ),
    "transactions_page": (
        "One sentence that names the card and the period, newest first, then the list on a line of its own, and"
        " nothing after it."
    ),
    "transactions_next": (
        "One sentence that says these are the card's next transactions in the period, then the list on a line of"
        " its own, and nothing after it."
    ),
    "decline_explained": (
        "One sentence that says this declined transaction was found on the card, then the transaction on a line of its"
        " own, with the reason on record on the line right under it, and nothing after them."
    ),
    "decline_status": (
        "One sentence that says this transaction was found on the card, then the transaction on a line of its own,"
        " then one sentence that says it stands as its status, not as declined."
    ),
}
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


async def confirm(state: State) -> dict[str, Any]:
    scope = SCOPE.get()
    target = state["target"]
    assert target is not None
    confirmation_id, at = str(uuid.uuid4()), scope.now()
    record = new_record(
        confirmation_id,
        bound(scope),
        scope.claims.customer_id,
        target["card_id"],
        target["reason"],
        at,
    )
    await asyncio.to_thread(scope.confirmations.create, record)
    await scope.turn.write(
        "confirmation",
        confirmation_id=confirmation_id,
        card_id=target["card_id"],
        reason=target["reason"],
        to="pending",
        cause="created",
        **{"from": None},
    )
    handoff_id = None
    if target["reason"] in DRAFTED:
        handoff_id = await drafted(scope, state["language"], target, confirmation_id)
    facts = {"card": card_facts(target), "reason": target["reason"]}
    label = state.get("label") or "block_card"
    rules = (
        ["POL-13", "POL-36", "POL-39"]
        if label == "unrecognized_charge"
        else ["POL-13", "POL-35", "POL-36"]
    )
    say = said(("confirm_prompt", facts))
    if target["reason"] == "unrecognized_charge":
        # POL-39: the charge goes to a person however the offer ends, so the offer says so first.
        say = [*said(("charge_reviewed", {})), *say]
    if target["past_expiration"]:
        say = [*said(("past_expiration", facts)), *say]
        rules.append("POL-31")
    if not target["served_in_full"]:
        rules.append("POL-12")
    return {
        "case": "fixed",
        "asking": None,
        "pending": {
            "confirmation_id": confirmation_id,
            "card": card_facts(target),
            "card_id": target["card_id"],
            "reason": target["reason"],
            "expires_at": record["expires_at"],
            "origin_jti": scope.claims.origin_jti,
            "typed": 0,
            "listed": state.get("listed") or target.get("listed"),
            "label": label,
            "charge": target.get("charge"),
            "handoff_id": handoff_id,
        },
        "say": [*state.get("say", []), *say],
        "decision": decided("block", rules, CONTROL, label),
    }


async def drafted(
    scope: Scope, language: str, target: dict[str, Any], confirmation_id: str
) -> str:
    """
    Saves a draft of the handoff the confirmation's end may require, through file_handoff, since the Runtime's role
    writes no case: POL-39's however the confirmation ends, and POL-38's should a lost or stolen card's block lapse. The
    turn that ends the confirmation files it under the same ID, and a confirmation that ends with no turn leaves it to
    expire unfiled (ADR-0004, decision 7, deferred; D1). A draft that couldn't be saved is filed all the same.
    """
    handoff_id = str(uuid.uuid4())
    code = (
        "unrecognized_charge"
        if target["reason"] == "unrecognized_charge"
        else "block_lapsed"
    )
    call = await scope.filing.file(
        {
            "customer_id": scope.claims.customer_id,
            "origin_jti": scope.claims.origin_jti,
            "call_id": new_call_id(),
            "mode": "draft",
            "draft": {
                "handoff_id": handoff_id,
                "language": language,
                "reason_code": code,
                "confirmation_id": confirmation_id,
                "card_id": target["card_id"],
                "reason": target["reason"],
            },
        },
        scope.token,
        scope.turn.write,
        scope.retries,
        scope.elapsed,
    )
    if call.outcome == "ok" and (call.result or {}).get("status") == "draft_saved":
        # Urgent while the card isn't blocked (POL-47).
        await scope.turn.write(
            "handoff",
            handoff_id=handoff_id,
            reason_code=code,
            trigger="required",
            queue=HANDOFFS[code].queue,
            priority="urgent",
            status="draft_saved",
            flagged=False,
        )
    return handoff_id


def controls(state: State) -> dict[str, Any]:
    """
    The interrupt's value, built from the state alone: await_control runs again from its start when resumed. A pending
    confirmation and an offer show together when POL-36 offers a handoff while the confirmation is pending.
    """
    pending, offer = state.get("pending"), state.get("offer")
    shown: list[dict[str, Any]] = []
    if pending is not None:
        shown.append(
            {
                "kind": "block_confirmation",
                "confirmation_id": pending["confirmation_id"],
                "card": {
                    "type": pending["card"]["product_type"],
                    "last_four": pending["card"]["last_four"],
                },
                "reason": pending["reason"],
                "expires_at": wall_time(
                    datetime.fromtimestamp(pending["expires_at"], UTC)
                ),
            }
        )
    if offer is not None:
        shown.append(
            {
                "kind": "handoff_offer",
                "offer_id": offer["offer_id"],
                "reason_code": offer["reason_code"],
            }
        )
    return {"reason": "controls", "language": state["language"], "controls": shown}


def offer_waits(offer: dict[str, Any], rules: list[str]) -> dict[str, Any]:
    return {
        **concluded(offer["request"]["label"], offer["outcome_class"], rules),
        "awaiting": OFFERED,
    }


def unused(pending: dict[str, Any], outcome: str = "lapsed") -> dict[str, Any]:
    """
    A confirmation that ended unused, as a handoff's action: cancelled with the control, or lapsed.
    """
    return {
        "action": "block_card",
        "card_id": pending["card_id"],
        "reason": pending["reason"],
        "confirmation_id": pending["confirmation_id"],
        "outcome": outcome,
        "confirmed_at": None,
        "evidence": [],
    }


def owed(
    pending: dict[str, Any], cause: str, rules: list[str]
) -> dict[str, Any] | None:
    """
    The handoff a confirmation's unused end requires, which files its draft: POL-39's however it ended, and POL-38's
    when a lost or stolen card's block lapsed at its time limit or with the sign-in, since the customer left without
    blocking it. A cancel or a new request needs none for a lost card: the customer is there to decide.
    """
    if pending["reason"] == "unrecognized_charge":
        code, rule = "unrecognized_charge", "POL-39"
    elif pending["reason"] in MISSING and cause in ("time_limit", "session_end"):
        code, rule = "block_lapsed", "POL-38"
    else:
        return None
    return required(
        code,
        pending.get("label") or "block_card",
        list(dict.fromkeys([*rules, rule])),
        **cited(pending),
        actions=[
            unused(pending, "declined_by_customer" if cause == "control" else "lapsed")
        ],
        handoff_id=pending.get("handoff_id"),
    )


async def ended(scope: Scope, pending: dict[str, Any], to: str, cause: str) -> None:
    await scope.turn.write(
        "confirmation",
        confirmation_id=pending["confirmation_id"],
        card_id=pending["card_id"],
        reason=pending["reason"],
        to=to,
        cause=cause,
        **{"from": "pending"},
    )


async def lapsed(scope: Scope, pending: dict[str, Any], cause: str) -> list[list[Any]]:
    """
    Ends the confirmation unused, and says so; owed() says whether a person takes the case.
    """
    if await asyncio.to_thread(
        scope.confirmations.lapse, pending["confirmation_id"], cause, scope.now()
    ):
        await ended(scope, pending, "lapsed", cause)
    return said(("confirmation_lapsed", {"card": pending["card"]}))


def lapse_rules(pending: dict[str, Any], cause: str) -> list[str]:
    rules = ["POL-36"]
    if cause == "session_end":
        rules.insert(0, "POL-09")
    if cause != "message" and pending["reason"] in MISSING:
        rules.append("POL-38")
    return rules


async def await_control(state: State) -> dict[str, Any]:
    answer: dict[str, Any] = interrupt(controls(state))
    scope = SCOPE.get()
    pending, offer = state.get("pending"), state.get("offer")
    turn: dict[str, Any] = {
        "label": "block_card"
        if pending is not None or offer is None
        else offer["request"]["label"],
        "case": "fixed",
        "say": [],
        "decision": None,
        "rules": [],
        "details": None,
        "target": None,
        "block": None,
        "handoff": None,
        "then": None,
        "cards": [],
        "targets": [],
        "text": None,
        "parts": [],
        "names": [],
    }
    if answer["kind"] == "message":
        return {**turn, **(await typed(state, scope, answer))}
    if answer["kind"] in ("accept", "decline"):
        return {**turn, **(await answered_offer(state, scope, answer))}
    assert pending is not None
    # A confirm or a cancel ends an offer shown beside the confirmation.
    turn["offer"] = None
    confirmation_id = pending["confirmation_id"]
    if answer.get("confirmation_id") != confirmation_id:
        # The entrypoint lets through only an answer naming the pending control.
        await scope.turn.write(
            "resume",
            resume_kind=answer["kind"],
            accepted=False,
            refusal="not_pending",
        )
        return {
            **turn,
            "offer": offer,
            "case": "again",
            "decision": decided("block", ["POL-36"], CONTROL),
        }
    to = "confirmed" if answer["kind"] == "confirm" else "cancelled"
    at = scope.now()
    refusal = await asyncio.to_thread(
        scope.confirmations.answer, confirmation_id, to, bound(scope), at
    )
    await scope.turn.write(
        "resume",
        resume_kind=answer["kind"],
        confirmation_id=confirmation_id,
        accepted=refusal is None,
        **({} if refusal is None else {"refusal": refusal}),
    )
    if refusal is None and to == "confirmed":
        await ended(scope, pending, "confirmed", "control")
        return {
            **turn,
            "case": "blocking",
            "pending": {**pending, "confirmed_at": wall_time(at)},
        }
    if refusal is None:
        await ended(scope, pending, "cancelled", "control")
        cancelled = {
            **turn,
            "pending": None,
            "say": said(("confirmation_cancelled", {"card": pending["card"]})),
        }
        request = owed(pending, "control", ["POL-36"])
        if request is not None:
            return cancelled | {"case": "handoff", "handoff": request}
        return cancelled | {"decision": decided("answer", ["POL-36"])}
    cause = {"expired": "time_limit", "other_sign_in": "session_end"}.get(
        refusal, "message"
    )
    rules = lapse_rules(pending, cause)
    ended_unused = {
        **turn,
        "pending": None,
        "say": await lapsed(scope, pending, cause),
    }
    request = owed(pending, cause, rules)
    if request is not None:
        return ended_unused | {"case": "handoff", "handoff": request}
    return ended_unused | {"decision": decided("answer", rules)}


async def answered_offer(
    state: State, scope: Scope, answer: dict[str, Any]
) -> dict[str, Any]:
    """
    The handoff control's answer (POL-45). Accepting files the offered handoff and ends a confirmation shown beside it
    unused (POL-36); declining ends the offer, and a confirmation beside it stays pending. From another sign-in, the
    offer has lapsed with the session, and so has a confirmation beside it (POL-09).
    """
    offer, pending = state["offer"], state.get("pending")
    assert offer is not None
    if answer.get("offer_id") != offer["offer_id"]:
        await scope.turn.write(
            "resume",
            resume_kind=answer["kind"],
            accepted=False,
            refusal="not_pending",
        )
        waits = (
            decided("block", ["POL-36"], CONTROL)
            if pending is not None
            else offer_waits(offer, ["POL-45"])
        )
        return {"case": "again", "decision": waits}
    if offer["origin_jti"] != scope.claims.origin_jti:
        await scope.turn.write(
            "resume",
            resume_kind=answer["kind"],
            offer_id=offer["offer_id"],
            accepted=False,
            refusal="other_sign_in",
        )
        say, rules = said(("offer_lapsed", {})), ["POL-09", "POL-45"]
        update: dict[str, Any] = {"offer": None, "pending": None}
        if pending is not None:
            say = [*(await lapsed(scope, pending, "session_end")), *say]
            rules = [*lapse_rules(pending, "session_end"), "POL-45"]
            request = owed(pending, "session_end", rules)
            if request is not None:
                return update | {"say": say, "case": "handoff", "handoff": request}
        return update | {
            "say": say,
            "decision": concluded(offer["request"]["label"], "answer", rules),
        }
    await scope.turn.write(
        "resume",
        resume_kind=answer["kind"],
        offer_id=offer["offer_id"],
        accepted=True,
    )
    if answer["kind"] == "decline":
        declined = {"offer": None, "say": said(("offer_declined", {}))}
        if pending is not None:
            return declined | {
                "decision": decided("block", ["POL-36", "POL-45"], CONTROL)
            }
        return declined | {
            "decision": concluded(offer["request"]["label"], "answer", ["POL-45"])
        }
    request = offer["request"]
    update = {
        "case": "handoff",
        "offer": None,
        "label": request["label"],
    }
    if pending is not None:
        if await asyncio.to_thread(
            scope.confirmations.lapse,
            pending["confirmation_id"],
            "handoff_accepted",
            scope.now(),
        ):
            await ended(scope, pending, "lapsed", "handoff_accepted")
        # The accepted handoff files the confirmation's draft, if it had one, so none is left to expire.
        request = {
            **request,
            "actions": [*request["actions"], unused(pending)],
            "handoff_id": pending.get("handoff_id"),
        }
        update |= {
            "pending": None,
            "say": said(("confirmation_lapsed", {"card": pending["card"]})),
        }
    return update | {"handoff": request}


async def typed(state: State, scope: Scope, answer: dict[str, Any]) -> dict[str, Any]:
    """
    A message typed while a control shows (POL-06, POL-36, POL-45). A new request ends what shows and moves on, and so
    does another card or reason for a pending block; anything else, a typed yes included, leaves the controls as they
    were and points to them. Past a confirmation's time limit, or from another sign-in, what showed has already ended,
    and the message is served as a new one.
    """
    pending, offer = state.get("pending"), state.get("offer")
    text = answer["text"]
    update: dict[str, Any] = {
        "messages": [HumanMessage(id=answer["message_id"], content=text)]
    }
    await scope.turn.write(
        "resume",
        resume_kind="message",
        **({} if pending is None else {"confirmation_id": pending["confirmation_id"]}),
        **({} if offer is None else {"offer_id": offer["offer_id"]}),
        accepted=True,
    )
    try:
        labeled: RouterOutput | None = await scope.models.route(text, paged(state))
    except ModelFailedError:
        labeled = None
    if labeled is not None:
        update["language"] = settled(state.get("language", DEFAULT), labeled.language)
    if labeled is not None and labeled.language == OTHER:
        # POL-51: not a request, so what shows stays, and the chat points to it.
        if pending is not None:
            pointer, waits = (
                "control_pointer",
                decided("block", ["POL-51", "POL-36"], CONTROL),
            )
        else:
            assert offer is not None
            pointer, waits = "offer_pointer", offer_waits(offer, ["POL-51", "POL-45"])
        return update | {
            "case": "pointer",
            "say": said(("third_language", {}), (pointer, {})),
            "decision": waits,
        }
    routed = None if labeled is None else routing(labeled, text)
    if pending is None:
        assert offer is not None
        return update | (await typed_to_offer(scope, offer, routed))
    return update | (await typed_to_confirmation(scope, pending, offer, text, routed))


async def typed_to_offer(
    scope: Scope, offer: dict[str, Any], routed: dict[str, Any] | None
) -> dict[str, Any]:
    if offer["origin_jti"] != scope.claims.origin_jti:
        update: dict[str, Any] = {
            "offer": None,
            "say": said(("offer_lapsed", {})),
            "rules": ["POL-09", "POL-45"],
        }
        if routed is None:
            return update | {"case": "unavailable", "label": None}
        if routed["case"] == "no_request":
            return update | {
                "decision": concluded(None, "answer", ["POL-09", "POL-45"])
            }
        return update | routed
    if routed is None:
        return {
            "case": "pointer",
            "say": said(("unavailable", {})),
            "decision": offer_waits(offer, ["POL-48"]),
        }
    if routed["case"] != "no_request":
        return routed | {"offer": None, "rules": ["POL-45"]}
    return {
        "case": "pointer",
        "say": said(("offer_pointer", {})),
        "decision": offer_waits(offer, ["POL-06", "POL-45"]),
    }


async def typed_to_confirmation(
    scope: Scope,
    pending: dict[str, Any],
    offer: dict[str, Any] | None,
    text: str,
    routed: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    An offer shown beside the confirmation ends with it. The second time the chat points to the confirm control, it
    also offers a handoff, since a person can block the card, unless POL-39 already requires one (POL-36).
    """
    cause = None
    if pending["expires_at"] <= scope.now().timestamp():
        cause = "time_limit"
    elif pending["origin_jti"] != scope.claims.origin_jti:
        cause = "session_end"
    if cause is not None:
        say, rules = await lapsed(scope, pending, cause), lapse_rules(pending, cause)
        update: dict[str, Any] = {
            "pending": None,
            "offer": None,
            "say": say,
            "rules": rules,
        }
        request = owed(pending, cause, rules)
        if request is not None:
            # Filed first; a new request in the message is served after it.
            onward = (
                None if routed is None or routed["case"] == "no_request" else routed
            )
            return update | {"case": "handoff", "handoff": request, "then": onward}
        if routed is None:
            return update | {"case": "unavailable", "label": None}
        if routed["case"] == "no_request":
            return update | {"decision": decided("answer", rules)}
        return update | routed
    if routed is None:
        return {
            "case": "pointer",
            "say": said(("unavailable", {})),
            "decision": decided("abstain", ["POL-48"], CONTROL),
        }
    if routed["case"] not in ("block", "no_request") or routed["label"] not in (
        None,
        "block_card",
    ):
        moved: dict[str, Any] = {
            "pending": None,
            "offer": None,
            "say": await lapsed(scope, pending, "message"),
            "rules": ["POL-36"],
        }
        request = owed(pending, "message", ["POL-36"])
        if request is not None:
            return moved | {"case": "handoff", "handoff": request, "then": routed}
        return routed | moved
    try:
        details = (
            await scope.models.extract(
                text, "A block is waiting for the customer to confirm it."
            )
        ).details()
    except ModelFailedError:
        details = {"card_type": None, "last_four": None, "block_reason": None}
    card_type, last_four = hints(details)
    reason = details.get("block_reason")
    if not fits(pending["card"], card_type, last_four) or reason not in (
        None,
        pending["reason"],
    ):
        say = await lapsed(scope, pending, "message")
        if card_type is None and last_four is None:
            # Another reason for the same card: the card carries over, as the reason does for another card.
            details |= {
                "card_type": KINDS[pending["card"]["product_type"]],
                "last_four": pending["card"]["last_four"],
            }
        another: dict[str, Any] = {
            "pending": None,
            "offer": None,
            "say": say,
            "rules": ["POL-36"],
            "details": details | {"block_reason": reason or pending["reason"]},
        }
        onward = {"case": "block", "label": "block_card"}
        request = owed(pending, "message", ["POL-36"])
        if request is not None:
            return another | {"case": "handoff", "handoff": request, "then": onward}
        return another | onward
    pointers = pending["typed"] + 1
    rules = ["POL-06", "POL-36"]
    say = said(("control_pointer", {}))
    if (
        offer is None
        and pointers >= QUESTIONS
        and pending["reason"] != "unrecognized_charge"
    ):
        offer = offering(
            offered(
                "clarification_failed",
                "block_card",
                ["POL-36"],
                rules,
                calls=[pending["listed"]] if pending.get("listed") else [],
                cards=[pending["card_id"]],
            ),
            "block",
        )
        say += said(("handoff_offer", {}))
    return {
        "case": "pointer",
        "pending": {**pending, "typed": pointers},
        "offer": offer,
        "say": say,
        "decision": decided("block", rules, CONTROL),
    }


async def block(state: State) -> dict[str, Any]:
    scope = SCOPE.get()
    pending = state["pending"]
    assert pending is not None
    call = await tool(
        scope,
        "block_card",
        card_id=pending["card_id"],
        reason=pending["reason"],
        confirmation_id=pending["confirmation_id"],
    )
    if call.outcome == "ok" and call.result is not None:
        await scope.turn.write(
            "confirmation",
            confirmation_id=pending["confirmation_id"],
            card_id=pending["card_id"],
            reason=pending["reason"],
            to="consumed",
            cause="block",
            attempts=call.result["attempts"],
            block_outcome=call.result["block_outcome"],
            **{"from": "confirmed"},
        )
    return {
        "block": {
            "call_id": call.call_id,
            "outcome": call.outcome,
            "result": call.result,
            "planned": bool((call.error or {}).get("planned")),
        }
    }


async def verify(state: State) -> dict[str, Any]:
    """
    Reads the card again for the reply's and the handoff's evidence. The reply says it is blocked only when the block's
    read-back and this read both show it; otherwise the case is handed off, with the block's outcome as its action when
    the block returned one (POL-37, AI-05).
    """
    scope = SCOPE.get()
    pending, done = state["pending"], state["block"]
    assert pending is not None and done is not None
    call = await tool(scope, "get_card", card_id=pending["card_id"])
    facts = {"card": pending["card"]}
    result = done["result"] or {}
    read = (call.result or {}).get("card", {}).get("product_status")
    turn: dict[str, Any] = {"pending": None, "case": "fixed"}
    if "denied" in (done["outcome"], call.outcome):
        return turn | {"case": "refused"}
    if done["outcome"] == "refused" and result.get("refusal") == "not_active":
        card = {**pending["card"], "product_status": result["product_status"]}
        name = (
            "already_blocked"
            if card["product_status"] == "Blocked"
            else "not_blockable"
        )
        return turn | {
            "say": said((name, {"card": card})),
            "decision": decided("decline", ["POL-34"]),
        }
    actions = []
    if result.get("block_outcome") is not None:
        actions.append(
            {
                "action": "block_card",
                "card_id": pending["card_id"],
                "reason": pending["reason"],
                "confirmation_id": pending["confirmation_id"],
                "outcome": result["block_outcome"],
                "confirmed_at": pending["confirmed_at"],
                "evidence": [done["call_id"]],
            }
        )
    if result.get("block_outcome") == "verified" and read == "Blocked":
        say = said(("block_verified", facts))
        rules = ["POL-36", "POL-37"]
        if pending["reason"] in MISSING:
            # A replacement is a person's to arrange: offered, not required (POL-38).
            say += said(("replacement_by_person", {}), ("handoff_offer", {}))
            rules.append("POL-38")
            request = offered(
                "unsupported_request",
                "block_card",
                ["POL-38"],
                rules,
                calls=[done["call_id"], call.call_id],
                cards=[pending["card_id"]],
                actions=actions,
                # Accepted, it files the block's draft, which is otherwise left to expire.
                handoff_id=pending.get("handoff_id"),
            )
            return turn | {
                "say": say,
                "offer": offering(request, "block"),
                "decision": decided("block", rules, OFFERED),
            }
        if pending["reason"] == "unrecognized_charge":
            # The block is verified, and the charge goes to dispute intake all the same (POL-39).
            rules.append("POL-39")
            return turn | {
                "say": say,
                "case": "handoff",
                "handoff": required(
                    "unrecognized_charge",
                    pending.get("label") or "block_card",
                    rules,
                    **cited(pending, done["call_id"], call.call_id),
                    actions=actions,
                    handoff_id=pending.get("handoff_id"),
                ),
            }
        return turn | {"say": say, "decision": decided("block", rules)}
    # POL-39's handoff is the request's only one: a block for the charge, offered or asked for, that isn't verified is
    # recorded in it.
    charge = pending["reason"] == "unrecognized_charge"
    scope.turn.emit(
        "block_not_verified",
        reason=pending["reason"],
        planned=bool(done.get("planned") or (call.error or {}).get("planned")),
    )
    return turn | {
        "case": "handoff",
        "say": said(("block_not_verified", facts)),
        "handoff": required(
            "unrecognized_charge" if charge else "action_not_verified",
            pending.get("label") or "block_card",
            ["POL-37", "POL-39"] if charge else ["POL-37"],
            **cited(pending, done["call_id"], call.call_id),
            actions=actions,
            reported=pending["reason"],
            handoff_id=pending.get("handoff_id"),
        ),
    }


async def handoff(state: State) -> dict[str, Any]:
    """
    Files the handoff a node required. Code builds the payload from the evidence the thread's tool calls left in state,
    the model writes its free text, and file_handoff files it under the customer's own token, three attempts at most
    under one handoff ID, the draft's when one was saved. The reply gives the case's reference in fixed text, or says it
    couldn't be passed on (POL-45 to POL-48). A new request in the message that ended a confirmation is served after
    it (then).
    """
    scope = SCOPE.get()
    request = state["handoff"]
    assert request is not None
    cited = [
        c
        for c in state.get("evidence", [])
        if c["call_id"] in request["calls"]
        and c.get("sign_in") == scope.claims.origin_jti
    ]
    payload = built(
        request,
        cited,
        handoff_id=request.get("handoff_id") or str(uuid.uuid4()),
        created_at=wall_time(scope.now()),
        customer_id=scope.claims.customer_id,
        session_id=scope.claims.origin_jti,
        versions={"policy": POLICY_VERSION, "snapshot": scope.snapshot},
        business_date=scope.business_date,
        language=state["language"],
    )
    try:
        text = await scope.models.handoff_text(
            transcript(state["messages"]), context(request), done(request)
        )
    except ModelFailedError:
        text = None
    turns = sorted({c["turn"] for c in cited} - {scope.turn.prefix})
    call = await scope.filing.file(
        {
            "customer_id": scope.claims.customer_id,
            "origin_jti": scope.claims.origin_jti,
            "call_id": new_call_id(),
            "mode": "file",
            "payload": written(payload, text),
            "turns": [*turns[-(TURNS - 1) :], scope.turn.prefix],
        },
        scope.token,
        scope.turn.write,
        scope.retries,
        scope.elapsed,
    )
    result = call.result or {}
    say, rules = state.get("say", []), request["decided"]
    if call.outcome != "ok" or result.get("status") not in ("filed", "already_filed"):
        outcome = "abstain"
        say, rules = [*say, *said(("handoff_failed", {}))], [*rules, "POL-48"]
    else:
        await scope.turn.write(
            "handoff",
            handoff_id=payload["handoff_id"],
            reference=result["reference"],
            reason_code=request["reason_code"],
            trigger=request["trigger"],
            queue=result["queue"],
            priority=result["priority"],
            status=result["status"],
            flagged=result["flagged"],
            validation_errors=result["validation_errors"],
        )
        outcome = request["outcome"]
        say = [*say, *said(("handoff_filed", {"reference": result["reference"]}))]
        rules = [*rules, "POL-45"]
    then = state.get("then")
    if then is not None:
        # The request the handoff ends gets its own decision, before the new one is served (POL-05).
        after = [then["label"], *((then.get("queue") or {}).get("labels", []))]
        scope.turn.decide(
            **concluded(
                request["label"],
                outcome,
                list(dict.fromkeys([*state.get("rules", []), *rules])),
            ),
            pending_labels=[label for label in after if label is not None],
            language=state["language"],
        )
        return {**then, "then": None, "handoff": None, "say": say, "rules": []}
    return {
        "case": "fixed",
        "handoff": None,
        "say": say,
        "decision": concluded(request["label"], outcome, rules),
    }


# The decisions of turns whose reply is fixed text only, by case.
OUTCOMES_BY_CASE: dict[str, tuple[str, list[str]]] = {
    "no_request": ("answer", ["POL-06"]),
    "unavailable": ("abstain", ["POL-48"]),
    "refused": ("decline", ["POL-08", "POL-49"]),
    "other_person": ("decline", ["POL-08"]),
    "third_language": ("decline", ["POL-51"]),
}
# A request about someone else's card gets the refusal's words (POL-08).
FIXED["other_person"] = FIXED["refused"]
ASKED = {"card": "card", "reason": "reason", "transaction": "transaction"}


def decision(state: State, case: str) -> dict[str, Any]:
    outcome_class, rules = OUTCOMES_BY_CASE[case]
    label = None if case in ("no_request", "third_language") else state.get("label")
    asking = state.get("asking")
    awaiting = (
        ASKED[asking["detail"]]
        if case == "third_language" and asking is not None
        else "none"
    )
    return {
        "request_label": label,
        "outcome_class": outcome_class,
        "awaiting": awaiting,
        "rules": rules,
    }


def instructions(
    state: State, label: str, part: dict[str, Any], facts: list[str]
) -> str:
    """
    What the reply's model reads besides the request's message: the request, the records in words, the answer's shape,
    and each placeholder with what it holds, never its value.
    """
    listed = "\n".join(f"- {{{name}}}: {DESCRIPTIONS[name]}" for name in facts)
    shape = SHAPES.get(part.get("template", ""))
    return (
        f"Request: {label}.\nWhat the records show: {part['shown']}\n"
        + (f"Shape: {shape}\n" if shape else "")
        + f"Placeholders, each to be written once:\n{listed}"
    )


async def answer(
    state: State, scope: Scope, label: str | None, part: dict[str, Any]
) -> tuple[str, list[str]]:
    """
    A read's answer: the model's, when its request is among WRITES and the reply check passes it, filled in; otherwise
    the fixed reply, which states the same facts. Each answer the model wrote leaves a reply_check entry (decision 8). A
    model call that fails raises, since POL-48 then answers for the request.
    """
    language = state["language"]
    template = FIXED[part["template"]][language]
    filled = values(language, part["facts"])
    facts = {name: filled[name] for name in dict.fromkeys(placeholders(template))}
    fixed = fill(template, facts)
    if label not in WRITES:
        return fixed, [part["template"]]
    text = await scope.models.reply(
        request_text(state),
        instructions(state, label, part, list(facts)),
        LANGUAGE_NAMES[language],
    )
    broken = failures(text, facts)
    await scope.turn.write(
        "reply_check", passed=not broken, failures=broken, fell_back=bool(broken)
    )
    if broken:
        return fixed, [part["template"]]
    return fill(text, facts).strip(), []


async def conclude(state: State) -> dict[str, Any]:
    """
    Ends the request being served: its part of the turn's reply, from the fixed texts and the answer its nodes left, and
    its decision, with the requests still queued after it (POL-05). A read or a model call that failed can't be answered
    now, and a person is offered (POL-48).
    """
    scope = SCOPE.get()
    case, language = state["case"], state["language"]
    label = state.get("label")
    names: list[str] = []
    parts: list[str] = []
    for name, facts in state.get("say", []):
        if name != WRITE:
            names.append(name)
            parts.append(render(name, language, facts))
            continue
        try:
            text, used = await answer(state, scope, label, facts)
        except ModelFailedError:
            # What was said before it stands, such as a confirmation that ended unused (POL-36).
            case = "unavailable"
            break
        names += used
        parts.append(text)
    if case in FIXED:
        name = small_talk(state) if case == "no_request" else case
        names.append(name)
        parts.append(FIXED[name][language])
    explicit = state.get("decision")
    outcome = decision(state, case) if explicit is None or case in FIXED else explicit
    offer = state.get("offer") if case != "unavailable" else None
    if case == "unavailable" and label is not None:
        # A routed request whose read or model call failed; a failed router leaves no label to offer under (POL-48).
        calls = [
            c["call_id"]
            for c in state.get("evidence", [])
            if c["turn"] == scope.turn.prefix
        ]
        request = offered("tool_failure", label, ["POL-48"], ["POL-48"], calls=calls)
        offer = offering(request, "abstain")
        names.append("handoff_offer")
        parts.append(render("handoff_offer", language, {}))
        outcome = {**outcome, "awaiting": OFFERED}
    outcome = {
        "awaiting": "none",
        **outcome,
        "rules": list(dict.fromkeys([*outcome["rules"], *state.get("rules", [])])),
    }
    scope.turn.decide(**outcome, pending_labels=queued(state), language=language)
    return {
        "parts": [*state.get("parts", []), "\n\n".join(parts)],
        "names": [*state.get("names", []), *names],
        "say": [],
        "rules": [],
        "offer": offer,
        "decision": outcome,
    }


async def reply(state: State, config: RunnableConfig) -> dict[str, Any]:
    """
    The turn's reply, one message: a part for each request it served and, while a request waits on the customer, a
    sentence naming the ones queued after it (POL-05).
    """
    scope = SCOPE.get()
    language = state["language"]
    parts, names = list(state.get("parts", [])), list(state.get("names", []))
    if queued(state):
        names.append("queued")
        parts.append(render("queued", language, {"requests": queued(state)}))
    text = "\n\n".join(part for part in parts if part)
    message_id = uuid.uuid4().hex
    await scope.turn.write(
        "reply",
        message_id=message_id,
        text=text,
        language=language,
        fixed_texts=list(dict.fromkeys(names)),
    )
    await adispatch_custom_event(
        EMIT_MESSAGE, {"message_id": message_id, "message": text}, config=config
    )
    return {
        "messages": [AIMessage(id=message_id, content=text)],
        "parts": [],
        "names": [],
    }


async def hold(state: State) -> dict[str, Any]:
    """
    Records the decision of a turn that changed nothing and ends it at the control again.
    """
    explicit = state.get("decision")
    assert explicit is not None
    SCOPE.get().turn.decide(
        **explicit, pending_labels=queued(state), language=state["language"]
    )
    return {}


def after_begin(state: State) -> str:
    asking = state.get("asking")
    if asking is None:
        return "route"
    return "find_transaction" if asking["detail"] == "transaction" else "resolve_card"


STARTS = {"block": "resolve_card", "read": "resolve_card", "status": "list_cards"}


def serve(state: State) -> str:
    return STARTS.get(state["case"], "conclude")


def after_list(state: State) -> str:
    return {"handoff": "handoff", "unsupported": "unsupported"}.get(
        state["case"], "conclude"
    )


def after_resolve(state: State) -> str:
    return {
        "confirm": "confirm",
        "ask_reason": "ask_reason",
        "find": "find_transaction",
        "reading": "read",
        "handoff": "handoff",
        "aside": "aside",
    }.get(state["case"], "conclude")


def after_find(state: State) -> str:
    return {"confirm": "confirm", "handoff": "handoff", "aside": "aside"}.get(
        state["case"], "conclude"
    )


def after_aside(state: State) -> str:
    return "handoff" if state["case"] == "handoff" else serve(state)


def onward(state: State) -> str:
    return "handoff" if state["case"] == "handoff" else "conclude"


def after_conclude(state: State) -> str:
    """
    The next queued request is served once the one before it waits on nothing (POL-05).
    """
    awaiting = (state.get("decision") or {}).get("awaiting", "none")
    idle = all(state.get(k) is None for k in ("pending", "offer", "asking"))
    return "next_request" if awaiting == "none" and idle and queued(state) else "reply"


def after_reply(state: State) -> str:
    awaiting = (state.get("decision") or {}).get("awaiting")
    if awaiting == CONTROL and state.get("pending"):
        return "await_control"
    if awaiting == OFFERED and state.get("offer"):
        return "await_control"
    return END


def after_control(state: State) -> str:
    if state["case"] == "blocking":
        return "block"
    if state["case"] == "handoff":
        return "handoff"
    if state["case"] == "again":
        return "hold"
    return serve(state)


def build(
    checkpointer: BaseCheckpointSaver[Any],
) -> CompiledStateGraph[Any, Any, Any, Any]:
    graph = StateGraph(State, input_schema=ChatState, output_schema=ChatState)
    for node in (
        begin, route, next_request, list_cards, unsupported, resolve_card, read,
        find_transaction, aside, ask_reason, confirm, await_control, hold, block,
        verify, handoff, conclude, reply,
    ):  # fmt: skip
        graph.add_node(node.__name__, citing(node))
    starts = ["resolve_card", "list_cards", "conclude"]
    graph.add_edge(START, "begin")
    graph.add_conditional_edges(
        "begin", after_begin, ["route", "resolve_card", "find_transaction"]
    )
    graph.add_conditional_edges("route", serve, starts)
    graph.add_conditional_edges("next_request", serve, starts)
    graph.add_conditional_edges(
        "list_cards", after_list, ["handoff", "unsupported", "conclude"]
    )
    graph.add_conditional_edges("unsupported", onward, ["handoff", "conclude"])
    graph.add_conditional_edges(
        "resolve_card",
        after_resolve,
        [
            "confirm",
            "ask_reason",
            "find_transaction",
            "read",
            "aside",
            "handoff",
            "conclude",
        ],
    )
    graph.add_conditional_edges("read", onward, ["handoff", "conclude"])
    graph.add_conditional_edges(
        "find_transaction", after_find, ["confirm", "aside", "handoff", "conclude"]
    )
    graph.add_conditional_edges("aside", after_aside, ["handoff", *starts])
    graph.add_edge("ask_reason", "conclude")
    graph.add_edge("confirm", "conclude")
    graph.add_conditional_edges("conclude", after_conclude, ["next_request", "reply"])
    graph.add_conditional_edges("reply", after_reply, ["await_control", END])
    graph.add_conditional_edges(
        "await_control",
        after_control,
        ["block", "resolve_card", "list_cards", "handoff", "hold", "conclude"],
    )
    graph.add_edge("hold", "await_control")
    graph.add_edge("block", "verify")
    graph.add_conditional_edges("verify", onward, ["handoff", "conclude"])
    graph.add_conditional_edges("handoff", serve, starts)
    return graph.compile(checkpointer=checkpointer)
