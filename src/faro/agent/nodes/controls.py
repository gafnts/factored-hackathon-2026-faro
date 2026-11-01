"""
The confirm and handoff controls: waiting on them, and every answer they get, a message typed while they show included
(POL-36, POL-45).
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import interrupt

from faro.agent.language import DEFAULT, OTHER, settled
from faro.agent.models import ModelFailedError, RouterOutput
from faro.agent.nodes.calls import bound
from faro.agent.nodes.cards import KINDS, fits, hints
from faro.agent.nodes.state import (
    CONTROL,
    MISSING,
    OFFERED,
    QUESTIONS,
    Decision,
    Offer,
    Pending,
    Said,
    State,
    cited,
    decided,
    offering,
    paged,
    queued,
    routing,
    said,
)
from faro.agent.payload import Action, ActionOutcome, HandoffRequest, offered, required
from faro.agent.records import wall_time
from faro.agent.scope import SCOPE, Scope


def controls(state: State) -> dict[str, Any]:
    """
    The interrupt's value, built from the state alone: await_control runs again from its start when resumed. A pending
    confirmation and an offer show together when POL-36 offers a handoff while the confirmation is pending.
    """
    pending, offer = state.get("pending"), state.get("offer")
    showing: list[dict[str, Any]] = []
    if pending is not None:
        showing.append(
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
        showing.append(
            {
                "kind": "handoff_offer",
                "offer_id": offer["offer_id"],
                "reason_code": offer["reason_code"],
            }
        )
    return {"reason": "controls", "language": state["language"], "controls": showing}


def offer_waits(offer: Offer, rules: list[str]) -> Decision:
    return decided(offer["outcome_class"], rules, offer["request"]["label"], OFFERED)


def unused(pending: Pending, outcome: ActionOutcome = "lapsed") -> Action:
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


def owed(pending: Pending, cause: str, rules: list[str]) -> HandoffRequest | None:
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


async def ended(scope: Scope, pending: Pending, to: str, cause: str) -> None:
    await scope.turn.write(
        "confirmation",
        confirmation_id=pending["confirmation_id"],
        card_id=pending["card_id"],
        reason=pending["reason"],
        to=to,
        cause=cause,
        **{"from": "pending"},
    )


async def lapsed(scope: Scope, pending: Pending, cause: str) -> list[Said]:
    """
    Ends the confirmation unused, and says so; owed() says whether a person takes the case.
    """
    if await asyncio.to_thread(
        scope.confirmations.lapse, pending["confirmation_id"], cause, scope.now()
    ):
        await ended(scope, pending, "lapsed", cause)
    return said(("confirmation_lapsed", {"card": pending["card"]}))


def lapse_rules(pending: Pending, cause: str) -> list[str]:
    rules = ["POL-36"]
    if cause == "session_end":
        rules.insert(0, "POL-09")
    if cause != "message" and pending["reason"] in MISSING:
        rules.append("POL-38")
    return rules


async def await_control(state: State) -> dict[str, Any]:
    resumed: dict[str, Any] = interrupt(controls(state))
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
    if resumed["kind"] == "message":
        return {**turn, **(await typed(state, scope, resumed))}
    if resumed["kind"] in ("accept", "decline"):
        return {**turn, **(await answered_offer(state, scope, resumed))}
    assert pending is not None
    # A confirm or a cancel ends an offer shown beside the confirmation.
    turn["offer"] = None
    confirmation_id = pending["confirmation_id"]
    if resumed.get("confirmation_id") != confirmation_id:
        # The entrypoint lets through only an answer naming the pending control.
        await scope.turn.write(
            "resume",
            resume_kind=resumed["kind"],
            accepted=False,
            refusal="not_pending",
        )
        return {
            **turn,
            "offer": offer,
            "case": "again",
            "decision": decided("block", ["POL-36"], "block_card", CONTROL),
        }
    to = "confirmed" if resumed["kind"] == "confirm" else "cancelled"
    at = scope.now()
    refusal = await asyncio.to_thread(
        scope.confirmations.answer, confirmation_id, to, bound(scope), at
    )
    await scope.turn.write(
        "resume",
        resume_kind=resumed["kind"],
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
        return cancelled | {"decision": decided("answer", ["POL-36"], "block_card")}
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
    return ended_unused | {"decision": decided("answer", rules, "block_card")}


async def answered_offer(
    state: State, scope: Scope, resumed: dict[str, Any]
) -> dict[str, Any]:
    """
    The handoff control's answer (POL-45). Accepting files the offered handoff and ends a confirmation shown beside it
    unused (POL-36); declining ends the offer, and a confirmation beside it stays pending. From another sign-in, the
    offer has lapsed with the session, and so has a confirmation beside it (POL-09).
    """
    offer, pending = state["offer"], state.get("pending")
    assert offer is not None
    if resumed.get("offer_id") != offer["offer_id"]:
        await scope.turn.write(
            "resume",
            resume_kind=resumed["kind"],
            accepted=False,
            refusal="not_pending",
        )
        waits = (
            decided("block", ["POL-36"], "block_card", CONTROL)
            if pending is not None
            else offer_waits(offer, ["POL-45"])
        )
        return {"case": "again", "decision": waits}
    if offer["origin_jti"] != scope.claims.origin_jti:
        await scope.turn.write(
            "resume",
            resume_kind=resumed["kind"],
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
            "decision": decided("answer", rules, offer["request"]["label"]),
        }
    await scope.turn.write(
        "resume",
        resume_kind=resumed["kind"],
        offer_id=offer["offer_id"],
        accepted=True,
    )
    if resumed["kind"] == "decline":
        declined = {"offer": None, "say": said(("offer_declined", {}))}
        if pending is not None:
            return declined | {
                "decision": decided(
                    "block", ["POL-36", "POL-45"], "block_card", CONTROL
                )
            }
        return declined | {
            "decision": decided("answer", ["POL-45"], offer["request"]["label"])
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
        request = request | {
            "actions": [*request["actions"], unused(pending)],
            "handoff_id": pending.get("handoff_id"),
        }
        update |= {
            "pending": None,
            "say": said(("confirmation_lapsed", {"card": pending["card"]})),
        }
    return update | {"handoff": request}


async def typed(state: State, scope: Scope, resumed: dict[str, Any]) -> dict[str, Any]:
    """
    A message typed while a control shows (POL-06, POL-36, POL-45). A new request ends what shows and moves on, and so
    does another card or reason for a pending block; anything else, a typed yes included, leaves the controls as they
    were and points to them. Past a confirmation's time limit, or from another sign-in, what showed has already ended,
    and the message is served as a new one.
    """
    pending, offer = state.get("pending"), state.get("offer")
    text = resumed["text"]
    update: dict[str, Any] = {
        "messages": [HumanMessage(id=resumed["message_id"], content=text)]
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
                decided("block", ["POL-51", "POL-36"], "block_card", CONTROL),
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
    scope: Scope, offer: Offer, routed: dict[str, Any] | None
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
            return update | {"decision": decided("answer", ["POL-09", "POL-45"], None)}
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
    pending: Pending,
    offer: Offer | None,
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
            then = None if routed is None or routed["case"] == "no_request" else routed
            return update | {"case": "handoff", "handoff": request, "then": then}
        if routed is None:
            return update | {"case": "unavailable", "label": None}
        if routed["case"] == "no_request":
            return update | {"decision": decided("answer", rules, "block_card")}
        return update | routed
    if routed is None:
        return {
            "case": "pointer",
            "say": said(("unavailable", {})),
            "decision": decided("abstain", ["POL-48"], "block_card", CONTROL),
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
        then = {"case": "block", "label": "block_card"}
        request = owed(pending, "message", ["POL-36"])
        if request is not None:
            return another | {"case": "handoff", "handoff": request, "then": then}
        return another | then
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
                calls=[pending["listed"]] if pending["listed"] else [],
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
        "decision": decided("block", rules, "block_card", CONTROL),
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
