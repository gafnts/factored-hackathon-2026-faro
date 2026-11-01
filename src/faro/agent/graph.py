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

import uuid
from typing import Any

from langchain_core.callbacks import adispatch_custom_event
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from faro.agent.check import failures
from faro.agent.filing import new_call_id
from faro.agent.models import ModelFailedError
from faro.agent.nodes.block import block, confirm, verify
from faro.agent.nodes.calls import citing
from faro.agent.nodes.controls import await_control, hold
from faro.agent.nodes.find import find_transaction
from faro.agent.nodes.reads import read
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
    OFFERED,
    READS,
    WRITE,
    ChatState,
    State,
    concluded,
    offering,
    queued,
    request_text,
    said,
    small_talk,
)
from faro.agent.payload import built, context, done, offered, transcript, written
from faro.agent.records import wall_time
from faro.agent.scope import SCOPE, Scope
from faro.agent.texts import FIXED, LANGUAGE_NAMES, fill, placeholders, render, values
from faro.policy import POLICY_VERSION

# The wrapper turns this event into one TEXT_MESSAGE_START, CONTENT, and END, so a reply reaches the chat whole.
EMIT_MESSAGE = "manually_emit_message"
# file_handoff reads at most this many of the sign-in's turns.
TURNS = 20
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
