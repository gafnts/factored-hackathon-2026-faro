"""
The tool calls a node makes, and how they reach the state's evidence.
"""

import dataclasses
import functools
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from faro.agent.gateway import ToolCall
from faro.agent.nodes.state import State, Update
from faro.agent.scope import SCOPE, Scope
from faro.tools.provenance import OUTCOMES


def bound(scope: Scope) -> dict[str, str]:
    return {
        "thread_key": scope.thread_key,
        "sub": scope.claims.sub,
        "origin_jti": scope.claims.origin_jti,
    }


async def tool(scope: Scope, name: str, **arguments: Any) -> ToolCall:
    """
    A failed attempt is tried again as decision 18 says, under the same call ID, and never a denied one (POL-48,
    POL-49); a handoff cites the call once, by its last attempt.
    """
    sent = {
        "customer_id": scope.claims.customer_id,
        "origin_jti": scope.claims.origin_jti,
        **arguments,
    }
    call_id, attempt, wait = str(uuid.uuid4()), 1, None
    while True:
        call = await scope.gateway.call(name, sent, scope.token, call_id, attempt)
        if wait is not None:
            call = dataclasses.replace(call, waited=wait.fields())
        wait = (
            scope.retries.after(attempt, scope.elapsed())
            if call.outcome == "failed"
            else None
        )
        await scope.turn.write("tool_call", last=wait is None, **call.entry())
        if wait is None:
            break
        await scope.retries.sleep(wait.seconds)
        attempt += 1
    scope.turn.cited.append(
        {
            "call_id": call.call_id,
            "tool": call.tool,
            "called_at": call.called_at,
            "outcome": OUTCOMES[call.outcome],
            "turn": scope.turn.prefix,
            "sign_in": scope.claims.origin_jti,
            "result": call.result,
        }
    )
    return call


Node = Callable[..., Awaitable[Update]]


def citing(node: Node) -> Node:
    """
    Hands the tool calls a node made to the state's evidence, with the turn each is recorded in, so a handoff filed in
    a later turn can cite them (ADR-0004, The handoff; ADR-0007's amendment of 2026-09-30).
    """

    @functools.wraps(node)
    async def run(state: State, *args: Any, **kwargs: Any) -> Update:
        update = await node(state, *args, **kwargs)
        calls = SCOPE.get().turn.drain()
        return {**update, "evidence": calls} if calls else update

    return run
