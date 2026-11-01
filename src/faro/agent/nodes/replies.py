"""
Each request's part of the reply and its decision, and the turn's reply, sent whole: fixed texts code chooses, and the
reads' answers the model writes and code checks (ADR-0004, decision 8).
"""

import uuid
from typing import Any

from langchain_core.callbacks import adispatch_custom_event
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from faro.agent.check import failures
from faro.agent.models import ModelFailedError
from faro.agent.nodes.state import (
    OFFERED,
    READS,
    WRITE,
    State,
    offering,
    queued,
    request_text,
    small_talk,
)
from faro.agent.payload import offered
from faro.agent.scope import SCOPE, Scope
from faro.agent.texts import FIXED, LANGUAGE_NAMES, fill, placeholders, render, values

# The wrapper turns this event into one TEXT_MESSAGE_START, CONTENT, and END, so a reply reaches the chat whole.
EMIT_MESSAGE = "manually_emit_message"
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
