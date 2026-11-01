"""
A handoff's payload, as the graph builds it (ADR-0004, The handoff; POL-45 to POL-47; CTL-05). Code fills every field
but three: each verified fact is one field of a record that a cited tool call read, the actions come from the
confirmation and the block's result, and the queue and the priority follow POL-47. The model writes the summary, the
customer's statements, and the open questions, each checked here against the handoff schema's text rule and lengths;
one that fails, or a call that fails, takes the reason code's fixed text (POL-46, OPS-05). file_handoff validates the
whole payload again and checks it against the execution record before it files it.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage

from faro.agent.models import BlockReason, HandoffText
from faro.masking import has_digit_run
from faro.policy import handoff_schema
from faro.policy.handoffs import HANDOFFS, MISSING, urgent
from faro.tools.file_handoff import ADDED
from faro.tools.provenance import rows

SCHEMA = handoff_schema()
FIELDS = {
    branch["if"]["properties"]["subject"]["const"]: tuple(
        f
        for f in branch["then"]["properties"]["field"]["enum"]
        if (branch["if"]["properties"]["subject"]["const"], f) not in ADDED
    )
    for branch in SCHEMA["$defs"]["fact"]["allOf"]
    if "subject" in branch["if"]["properties"]
}
KEYS = {"card": "card_id", "transaction": "transaction_id"}
TEXT = re.compile(SCHEMA["$defs"]["text"]["not"]["pattern"])
SUMMARY = SCHEMA["properties"]["request"]["properties"]["summary"]["maxLength"]
ITEM = SCHEMA["properties"]["customer_statements"]["items"]["maxLength"]
ITEMS = SCHEMA["properties"]["customer_statements"]["maxItems"]
# file_handoff adds the customer's status and each named transaction's is_fraud within the payload's 60.
FACTS = 40
# The latest messages the model reads; the payload carries no transcript (POL-46).
READ = 12
# The reasons a customer gives that make a handoff urgent while the card isn't verified blocked (POL-47).
REPORTED = (*MISSING, "unrecognized_charge")

ActionOutcome = Literal["verified", "not_verified", "declined_by_customer", "lapsed"]


class Action(TypedDict):
    """
    A block taken or offered, as the handoff schema's action records it (POL-36, POL-37).
    """

    action: Literal["block_card"]
    card_id: str
    reason: BlockReason
    confirmation_id: str
    outcome: ActionOutcome
    confirmed_at: str | None
    evidence: list[str]


class HandoffRequest(TypedDict):
    """
    A handoff as a node hands it to the handoff node, or as an offer holds it until the handoff control answers.
    """

    reason_code: str
    trigger: Literal["required", "accepted_offer"]
    label: str | None
    rules: list[str]
    decided: list[str]
    outcome: Literal["hand_off"]
    calls: list[str]
    cards: list[str]
    transactions: list[str]
    actions: list[Action]
    reported: str | None
    handoff_id: str | None


def required(
    reason_code: str,
    label: str | None,
    decided: list[str],
    *,
    calls: Sequence[str] = (),
    cards: Sequence[str] = (),
    actions: Sequence[Action] = (),
    reported: str | None = None,
    handoff_id: str | None = None,
    transactions: Sequence[str] = (),
) -> HandoffRequest:
    """
    A handoff the policy requires, as a node hands it to the handoff node: the calls whose results hold its facts, the
    records those facts are about, the actions, the reason the customer gave, the rules the turn's decision cites, and
    the ID of the draft it files, if one was saved. The turn that files it is hand_off, a verified block included
    (ADR-0005, as amended on 2026-10-02).
    """
    return {
        "reason_code": reason_code,
        "trigger": "required",
        "label": label,
        "rules": list(HANDOFFS[reason_code].rules),
        "decided": decided,
        "outcome": "hand_off",
        "calls": list(calls),
        "cards": list(cards),
        "transactions": list(transactions),
        "actions": [a.copy() for a in actions],
        "reported": reported,
        "handoff_id": handoff_id,
    }


def offered(
    reason_code: str,
    label: str | None,
    rules: list[str],
    decided: list[str],
    *,
    calls: Sequence[str] = (),
    cards: Sequence[str] = (),
    actions: Sequence[Action] = (),
    handoff_id: str | None = None,
    transactions: Sequence[str] = (),
) -> HandoffRequest:
    """
    A handoff the policy offers, filed only if the customer accepts it with the handoff control (POL-45). rules are the
    ones that offered it here, since a reason code may be offered under more than one.
    """
    return {
        **required(
            reason_code,
            label,
            decided,
            calls=calls,
            cards=cards,
            actions=actions,
            handoff_id=handoff_id,
            transactions=transactions,
        ),
        "trigger": "accepted_offer",
        "rules": rules,
    }


def scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def facts_of(
    calls: Sequence[Mapping[str, Any]],
    customer_id: str,
    cards: Sequence[str],
    transactions: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """
    Every field of the customer and of the named cards and transactions that the calls read, each once, from the latest
    call that read it, so a card read again after a block states its new status.
    """
    found: dict[tuple[str, str, str], dict[str, Any]] = {}

    def keep(subject: str, record: str, field: str, value: Any, call: str) -> None:
        if scalar(value):
            found[(subject, record, field)] = {
                "subject": subject,
                "id": record,
                "field": field,
                "value": value,
                "evidence": call,
            }

    for call in calls:
        result = call.get("result")
        if not isinstance(result, dict):
            continue
        customer = result.get("customer")
        if isinstance(customer, dict):
            for field in FIELDS["customer"]:
                if field in customer:
                    keep(
                        "customer", customer_id, field, customer[field], call["call_id"]
                    )
        for row in rows(result):
            if row.get("transaction_id") in transactions:
                subject, record = "transaction", row["transaction_id"]
            elif "transaction_id" not in row and row.get("card_id") in cards:
                subject, record = "card", row["card_id"]
            else:
                continue
            for field in FIELDS[subject]:
                if field in row:
                    keep(subject, record, field, row[field], call["call_id"])
    return list(found.values())[:FACTS]


def built(
    request: Mapping[str, Any],
    cited: Sequence[Mapping[str, Any]],
    *,
    handoff_id: str,
    created_at: str,
    customer_id: str,
    session_id: str,
    versions: Mapping[str, Any],
    business_date: str,
    language: str,
) -> dict[str, Any]:
    """
    The payload but its free text, which falls back to the reason code's until written() fills it in.
    """
    code = request["reason_code"]
    facts = facts_of(
        cited, customer_id, request["cards"], request.get("transactions", [])
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "handoff_id": handoff_id,
        "created_at": created_at,
        "session_id": session_id,
        "customer_id": customer_id,
        "versions": dict(versions),
        "business_date": business_date,
        "language": language,
        "queue": HANDOFFS[code].queue,
        "priority": "normal",
        "trigger": request["trigger"],
        "reason_code": code,
        "rules": request["rules"],
        "request": {"label": request["label"], "summary": HANDOFFS[code].summary},
        "verified_facts": facts,
        "actions": request["actions"],
        "evidence": [
            {k: c[k] for k in ("call_id", "tool", "called_at", "outcome")}
            for c in cited
        ],
        "customer_statements": [],
        "unresolved_questions": [],
    }
    blocked = any(a["outcome"] == "verified" for a in request["actions"])
    if urgent(payload) or (request["reported"] in REPORTED and not blocked):
        payload["priority"] = "urgent"
    return payload


def fits(text: Any, limit: int) -> bool:
    return (
        isinstance(text, str)
        and 0 < len(text) <= limit
        and TEXT.search(text) is None
        and not has_digit_run(text)
    )


def written(payload: dict[str, Any], text: HandoffText | None) -> dict[str, Any]:
    """
    The payload with the model's text wherever it passes the schema's rules: a summary that fails keeps the reason
    code's, and a statement or question that fails is left out.
    """
    if text is None:
        return payload
    summary = text.summary.strip()
    return {
        **payload,
        "request": {
            **payload["request"],
            "summary": summary
            if fits(summary, SUMMARY)
            else payload["request"]["summary"],
        },
        "customer_statements": [
            s.strip() for s in text.customer_statements if fits(s.strip(), ITEM)
        ][:ITEMS],
        "unresolved_questions": [
            q.strip() for q in text.unresolved_questions if fits(q.strip(), ITEM)
        ][:ITEMS],
    }


def transcript(messages: Sequence[AnyMessage]) -> str:
    """
    The conversation's latest messages, the customer's masked as the checkpoint holds them.
    """
    return "\n\n".join(
        f"{'Customer' if isinstance(m, HumanMessage) else 'Chat'}: {m.text}"
        for m in messages[-READ:]
    )


# What the chat did about the card, by each action's outcome, which the conversation the model reads doesn't show yet:
# the turn's reply is written after the handoff's text (POL-37, POL-39).
DONE = {
    "verified": (
        "The chat blocked the card at the customer's confirmation and verified the block: the card is blocked."
    ),
    "not_verified": (
        "The customer confirmed a block, but the chat couldn't verify it: the card's state is unknown."
    ),
    "declined_by_customer": (
        "The chat offered to block the card and the customer cancelled with the control: the card isn't blocked."
    ),
    "lapsed": (
        "The chat offered to block the card and the offer ended without a confirmation: the card isn't blocked."
    ),
}
NOTHING_DONE = "The chat didn't block a card or offer to in this conversation."


def context(request: Mapping[str, Any]) -> str:
    return f"Why the case goes to a person: {HANDOFFS[request['reason_code']].summary}"


def done(request: Mapping[str, Any]) -> str:
    """
    What the chat did about the card, as a block of its own after the context, so the model's notes agree with a block
    the turn's reply hasn't reported yet.
    """
    lines = [DONE[a["outcome"]] for a in request["actions"] if a["outcome"] in DONE]
    return "\n".join(lines or [NOTHING_DONE])
