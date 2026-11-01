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

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from faro.agent.nodes.block import block, confirm, verify
from faro.agent.nodes.calls import citing
from faro.agent.nodes.controls import await_control, hold
from faro.agent.nodes.find import find_transaction
from faro.agent.nodes.handoff import handoff
from faro.agent.nodes.reads import read
from faro.agent.nodes.replies import conclude, reply
from faro.agent.nodes.resolve import ask_reason, resolve_card
from faro.agent.nodes.routing import (
    aside,
    begin,
    list_cards,
    next_request,
    route,
    unsupported,
)
from faro.agent.nodes.state import CONTROL, OFFERED, ChatState, State, queued


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
    decision = state.get("decision")
    awaiting = "none" if decision is None else decision["awaiting"]
    idle = all(state.get(k) is None for k in ("pending", "offer", "asking"))
    return "next_request" if awaiting == "none" and idle and queued(state) else "reply"


def after_reply(state: State) -> str:
    decision = state.get("decision")
    awaiting = None if decision is None else decision["awaiting"]
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
    checkpointer: BaseCheckpointSaver[str],
) -> CompiledStateGraph[State, None, ChatState, ChatState]:
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
