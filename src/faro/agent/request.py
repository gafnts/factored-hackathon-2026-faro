"""
Reads the chat's request, never trusting it (ADR-0004's amendment on the wrapper's whole input; the chat's contract):
a run carries a new message, a resume, or neither (the warm-up), and the entrypoint builds the wrapper's input from that
alone. A resume is taken only when it answers the thread's pending control: its interrupt is the one the checkpoint
holds, and its payload names one of that interrupt's controls. Any other is refused before the graph runs, so a stale
control changes nothing (POL-09, POL-36). A new message sent while a control is pending becomes a resume of kind
message, since the wrapper would otherwise answer it with the interrupt again and drop it (The confirmation). A request
that fails the contract, or the checks the contract can't express, is refused with each failure's path and rule, never
its value (POL-11).
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from faro.contracts import validator
from faro.masking import mask
from faro.tools import MAX_ERRORS, pointer


@dataclass(frozen=True)
class NewMessage:
    id: str
    text: str


@dataclass(frozen=True)
class Warmup:
    pass


@dataclass(frozen=True)
class Resume:
    interrupt_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Pending:
    """
    The thread's pending interrupt, as the checkpoint holds it: its ID and the controls it shows.
    """

    interrupt_id: str
    controls: Sequence[dict[str, Any]]


# A control's answer names the control it answers by this key.
ANSWERS = {
    "confirm": ("block_confirmation", "confirmation_id"),
    "cancel": ("block_confirmation", "confirmation_id"),
    "accept": ("handoff_offer", "offer_id"),
    "decline": ("handoff_offer", "offer_id"),
}


class RequestRefusedError(Exception):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("invalid_request")
        self.errors = errors[:MAX_ERRORS]


def errors_of(definition: str, instance: Any, prefix: str = "") -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for error in validator("chat", definition).iter_errors(instance):
        entry = {"path": f"{prefix}{pointer(error)}", "rule": str(error.validator)}
        if entry not in found:
            found.append(entry)
    return sorted(found, key=lambda e: (e["path"], e["rule"]))


def check_contract(request: dict[str, Any]) -> None:
    errors = errors_of("request", request)
    if errors:
        raise RequestRefusedError(errors)


def refused(path: str, rule: str) -> RequestRefusedError:
    return RequestRefusedError([{"path": path, "rule": rule}])


def answered(entry: dict[str, Any], pending: Pending | None) -> Resume:
    if pending is None:
        raise refused("/resume", "notPending")
    if entry["interruptId"] != pending.interrupt_id:
        raise refused("/resume/0/interruptId", "notPending")
    payload = entry["payload"]
    kind, key = ANSWERS[payload["kind"]]
    if not any(
        control["kind"] == kind and control.get(key) == payload[key]
        for control in pending.controls
    ):
        raise refused(f"/resume/0/payload/{key}", "notPending")
    return Resume(pending.interrupt_id, dict(payload))


def read(
    request: dict[str, Any], held: Collection[str], pending: Pending | None = None
) -> NewMessage | Warmup | Resume:
    """
    request has passed check_contract; held is the message IDs the thread's checkpoint holds, and pending its
    interrupt, if any.
    """
    warmup = (request.get("forwardedProps") or {}).get("warmup") is True
    resume = bool(request.get("resume"))
    messages = request["messages"]
    last = len(messages) - 1
    new: dict[str, Any] | None = None
    if messages:
        message_errors = errors_of("user_message", messages[last], f"/messages/{last}")
        if not message_errors and messages[last]["id"] not in held:
            new = messages[last]
        elif not warmup and not resume:
            raise RequestRefusedError(
                message_errors or [{"path": f"/messages/{last}/id", "rule": "notNew"}]
            )
    if warmup and (new is not None or resume):
        raise refused("/forwardedProps/warmup", "conflict")
    if resume and new is not None:
        raise refused("/resume", "conflict")
    if resume:
        return answered(request["resume"][0], pending)
    if warmup:
        return Warmup()
    if new is None:
        raise refused("/messages", "notNew")
    text = mask(new["content"])
    if not text.strip():
        raise refused(f"/messages/{last}/content", "minLength")
    if pending is not None:
        return Resume(
            pending.interrupt_id,
            {"kind": "message", "message_id": new["id"], "text": text},
        )
    return NewMessage(new["id"], text)
