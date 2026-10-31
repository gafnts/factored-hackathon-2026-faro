"""
The policy's handoff reasons (docs/policy/card-support.md, Handoff reasons; POL-45 to POL-47): whether each is required
or offered, the rules behind it, and its queue, with the fixed summary in Spanish that a case falls back to when the
model's can't be used (ADR-0004, The handoff; OPS-05, POL-46).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

MISSING = ("lost", "stolen")


@dataclass(frozen=True)
class Reason:
    handoff: str
    rules: tuple[str, ...]
    queue: str
    summary: str


HANDOFFS: dict[str, Reason] = {
    "unrecognized_charge": Reason(
        "required",
        ("POL-39",),
        "dispute_intake",
        "El cliente no reconoce un cargo en su tarjeta.",
    ),
    "customer_request": Reason(
        "required",
        ("POL-44",),
        "customer_service",
        "El cliente pidió hablar con una persona.",
    ),
    "complaint": Reason(
        "required",
        ("POL-44",),
        "customer_service",
        "El cliente presentó un reclamo.",
    ),
    "unblock_request": Reason(
        "required",
        ("POL-41",),
        "customer_service",
        "El cliente pidió desbloquear una tarjeta.",
    ),
    "customer_not_active": Reason(
        "required",
        ("POL-12",),
        "customer_service",
        "El cliente hizo una solicitud que, por su estado en el banco, debe atender una persona.",
    ),
    "ambiguous_card": Reason(
        "required",
        ("POL-15",),
        "customer_service",
        "El cliente se refiere a una tarjeta que coincide con más de una de las suyas.",
    ),
    "action_not_verified": Reason(
        "required",
        ("POL-37",),
        "customer_service",
        "No se pudo confirmar el bloqueo de una tarjeta del cliente.",
    ),
    "block_lapsed": Reason(
        "required",
        ("POL-38",),
        "customer_service",
        "El cliente reportó una tarjeta perdida o robada y no confirmó su bloqueo.",
    ),
    "unsupported_request": Reason(
        "offered",
        ("POL-38", "POL-42"),
        "customer_service",
        "El cliente pidió un servicio que el chat no atiende.",
    ),
    "clarification_failed": Reason(
        "offered",
        ("POL-17", "POL-36"),
        "customer_service",
        "El chat no logró precisar la solicitud del cliente.",
    ),
    "record_conflict": Reason(
        "offered",
        ("POL-31",),
        "customer_service",
        "El cliente pregunta por datos contradictorios en los registros de su tarjeta.",
    ),
    "missing_data": Reason(
        "offered",
        ("POL-24", "POL-32"),
        "customer_service",
        "Falta en los registros un dato necesario para responder al cliente.",
    ),
    "tool_failure": Reason(
        "offered",
        ("POL-48",),
        "customer_service",
        "El chat no pudo consultar los registros del cliente.",
    ),
}


def urgent(payload: Mapping[str, Any]) -> bool:
    """
    Whether POL-47 makes the handoff urgent from what its payload shows: a card reported lost or stolen, or a charge the
    customer doesn't recognize, whose card no verified block or read shows blocked. The graph also knows a reason the
    customer gave before any block was offered, which the payload holds only in the customer's words.
    """
    actions = payload["actions"]
    facts = payload["verified_facts"]
    blocked = {a["card_id"] for a in actions if a["outcome"] == "verified"} | {
        f["id"]
        for f in facts
        if f["subject"] == "card"
        and f["field"] == "product_status"
        and f["value"] == "Blocked"
    }
    if {a["card_id"] for a in actions if a["reason"] in MISSING} - blocked:
        return True
    if "unrecognized_charge" in (payload["reason_code"], payload["request"]["label"]):
        cards = {a["card_id"] for a in actions} or {
            f["id"] for f in facts if f["subject"] == "card"
        }
        return not cards or not cards <= blocked
    return False
