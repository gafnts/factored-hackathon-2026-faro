"""
The handoff a node required, filed with the evidence the thread's tool calls left in state (POL-45 to POL-48).
"""

import uuid
from typing import Any

from faro.agent.filing import new_call_id
from faro.agent.models import ModelFailedError
from faro.agent.nodes.state import Outcome, State, decided, said
from faro.agent.payload import built, context, done, transcript, written
from faro.agent.records import wall_time
from faro.agent.scope import SCOPE
from faro.policy import POLICY_VERSION

# file_handoff reads at most this many of the sign-in's turns.
TURNS = 20


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
    sources = [
        c
        for c in state.get("evidence", [])
        if c["call_id"] in request["calls"]
        and c.get("sign_in") == scope.claims.origin_jti
    ]
    payload = built(
        request,
        sources,
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
    turns = sorted({c["turn"] for c in sources} - {scope.turn.prefix})
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
        outcome: Outcome = "abstain"
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
            **decided(
                outcome,
                list(dict.fromkeys([*state.get("rules", []), *rules])),
                request["label"],
            ),
            pending_labels=[label for label in after if label is not None],
            language=state["language"],
        )
        return {**then, "then": None, "handoff": None, "say": say, "rules": []}
    return {
        "case": "fixed",
        "handoff": None,
        "say": say,
        "decision": decided(outcome, rules, request["label"]),
    }
