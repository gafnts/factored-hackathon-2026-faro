"""
A block: its confirmation and the control that shows it, the block once confirmed, and the read that verifies it.
"""

import asyncio
import uuid
from typing import Any

from faro.agent.confirmations import new_record
from faro.agent.filing import new_call_id
from faro.agent.nodes.calls import bound, tool
from faro.agent.nodes.cards import card_facts
from faro.agent.nodes.state import (
    CONTROL,
    MISSING,
    OFFERED,
    State,
    Target,
    cited,
    decided,
    offering,
    said,
)
from faro.agent.payload import Action, offered, required
from faro.agent.scope import SCOPE, Scope
from faro.policy.handoffs import HANDOFFS

# The blocks whose confirmation's end may require a handoff, drafted when the control shows (POL-38, POL-39).
DRAFTED = (*MISSING, "unrecognized_charge")


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
    scope: Scope, language: str, target: Target, confirmation_id: str
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
    pending, tried = state["pending"], state["block"]
    assert pending is not None and tried is not None
    call = await tool(scope, "get_card", card_id=pending["card_id"])
    facts = {"card": pending["card"]}
    result = tried["result"] or {}
    status = (call.result or {}).get("card", {}).get("product_status")
    turn: dict[str, Any] = {"pending": None, "case": "fixed"}
    if "denied" in (tried["outcome"], call.outcome):
        return turn | {"case": "refused"}
    if tried["outcome"] == "refused" and result.get("refusal") == "not_active":
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
    actions: list[Action] = []
    if result.get("block_outcome") is not None:
        actions.append(
            {
                "action": "block_card",
                "card_id": pending["card_id"],
                "reason": pending["reason"],
                "confirmation_id": pending["confirmation_id"],
                "outcome": result["block_outcome"],
                "confirmed_at": pending["confirmed_at"],
                "evidence": [tried["call_id"]],
            }
        )
    if result.get("block_outcome") == "verified" and status == "Blocked":
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
                calls=[tried["call_id"], call.call_id],
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
                    **cited(pending, tried["call_id"], call.call_id),
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
        planned=bool(tried.get("planned") or (call.error or {}).get("planned")),
    )
    return turn | {
        "case": "handoff",
        "say": said(("block_not_verified", facts)),
        "handoff": required(
            "unrecognized_charge" if charge else "action_not_verified",
            pending.get("label") or "block_card",
            ["POL-37", "POL-39"] if charge else ["POL-37"],
            **cited(pending, tried["call_id"], call.call_id),
            actions=actions,
            reported=pending["reason"],
            handoff_id=pending.get("handoff_id"),
        ),
    }
