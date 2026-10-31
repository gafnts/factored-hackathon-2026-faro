"""
file_handoff (ADR-0004, The handoff and Where the tools run, and their amendments; ADR-0007, Handoffs as cases; POL-45 to
POL-47, CTL-05). It acts for the customer whose token the Runtime forwards, and for no one else: the input's customer and
sign-in, and the payload's, must be the token's. A draft is saved when the confirm control shows a block that may need
handing off, and a case is filed once: a retry with the same handoff ID files nothing and returns the first filing's
reference. Before filing, the tool adds what no Gateway tool returns (the customer's status and each named
transaction's is_fraud, a fixture's from the overlay of the handoff's sign-in, citing this call), checks the payload against the handoff schema and against the turns of the
execution record it names, raises a priority the payload shows should be urgent (POL-47), and files what passes,
flagged with each failure's path and rule when anything had to be left out or replaced.
"""

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from faro.contracts import validator
from faro.policy.handoffs import urgent
from faro.policy.payload import PayloadError, checked, failure
from faro.tools.cards import CustomerMissingError
from faro.tools.cases import (
    Cases,
    FraudFlags,
    draw_reference,
    queue_order,
    reference_item,
)
from faro.tools.identity import Caller, Verifier
from faro.tools.provenance import Records, checked_against
from faro.tools.sandbox import FixtureFlags, wall_time
from faro.tools.store import Record, ToolsData

RETAINED = timedelta(days=90)
DRAWS = 3
MAX_FACTS = 60
MAX_EVIDENCE = 60
MAX_ERRORS = 60
# Facts only this tool may state, from its own reads (POL-12, POL-40).
ADDED = {("customer", "customer_status"), ("transaction", "is_fraud")}
DRAFTED = ("language", "reason_code", "confirmation_id", "card_id", "reason")


@dataclass(frozen=True)
class HandoffStores:
    data: ToolsData
    flags: FraudFlags
    cases: Cases
    verifier: Verifier
    records: Records
    # The overlay, for a fixture transaction's is_fraud (ADR-0004's amendment of 2026-10-01).
    fixtures: FixtureFlags | None = None


def refused(refusal: str) -> dict[str, Any]:
    return {"outcome": "refused", "refusal": refusal}


def expires(at: datetime) -> int:
    return int((at + RETAINED).timestamp())


def save_draft(
    stores: HandoffStores, caller: Caller, arguments: dict[str, Any], now: datetime
) -> dict[str, Any]:
    draft = arguments["draft"]
    item = {
        "pk": draft["handoff_id"],
        "kind": "case",
        "handoff_id": draft["handoff_id"],
        "customer_id": caller.customer_id,
        "status": "draft",
        "source": caller.source,
        "saved_at": wall_time(now),
        "draft": {"sign_in": caller.origin_jti, **{k: draft[k] for k in DRAFTED}},
        "expires_at": expires(now),
    }
    validator("handoff-case").validate(item)
    if not stores.cases.save_draft(item):
        held = stores.cases.case(draft["handoff_id"])
        if held is None or held["customer_id"] != caller.customer_id:
            return refused("customer_mismatch")
    return {"outcome": "ok", "status": "draft_saved", "handoff_id": draft["handoff_id"]}


def already_filed(held: Record) -> dict[str, Any]:
    return {
        "outcome": "ok",
        "status": "already_filed",
        "handoff_id": held["handoff_id"],
        "reference": held["reference"],
        "queue": held["queue"],
        "priority": held["priority"],
        "added_facts": [],
        "flagged": held["flagged"],
        "validation_errors": held["validation_errors"],
    }


def added_facts(
    stores: HandoffStores, caller: Caller, facts: list[Any], call_id: str
) -> list[dict[str, Any]]:
    customer = stores.data.customer(caller.customer_id)
    if customer is None:
        raise CustomerMissingError()
    added = [
        {
            "subject": "customer",
            "id": caller.customer_id,
            "field": "customer_status",
            "value": customer["customer_status"],
            "evidence": call_id,
        }
    ]
    named = [
        f["id"]
        for f in facts
        if isinstance(f, dict)
        and f.get("subject") == "transaction"
        and isinstance(f.get("id"), str)
    ]
    for transaction_id in dict.fromkeys(named):
        flag = stores.flags.is_fraud(caller.customer_id, transaction_id)
        if flag is None and stores.fixtures is not None:
            flag = stores.fixtures.fixture_is_fraud(
                caller.origin_jti, caller.customer_id, transaction_id
            )
        if flag is not None:
            added.append(
                {
                    "subject": "transaction",
                    "id": transaction_id,
                    "field": "is_fraud",
                    "value": flag,
                    "evidence": call_id,
                }
            )
    return added


def completed(
    stores: HandoffStores,
    caller: Caller,
    given: dict[str, Any],
    call_id: str,
    now: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
    """
    The payload with this tool's facts and its own call among the evidence, before it is checked. A fact only this tool
    may state that arrives in the payload is left out, since nothing here read it.
    """
    payload = copy.deepcopy(given)
    errors: list[dict[str, str]] = []
    facts = payload.get("verified_facts")
    facts = facts if isinstance(facts, list) else []
    kept = []
    for i, fact in enumerate(facts):
        if isinstance(fact, dict) and (fact.get("subject"), fact.get("field")) in ADDED:
            errors.append(failure(["verified_facts", i], "reserved"))
        else:
            kept.append(fact)
    added = added_facts(stores, caller, kept, call_id)
    room = MAX_FACTS - len(added)
    if len(kept) > room:
        errors.append(failure(["verified_facts"], "maxItems"))
    payload["verified_facts"] = [*kept[:room], *added]
    evidence = payload.get("evidence")
    evidence = evidence if isinstance(evidence, list) else []
    if not any(isinstance(e, dict) and e.get("call_id") == call_id for e in evidence):
        own = {
            "call_id": call_id,
            "tool": "file_handoff",
            "called_at": wall_time(now),
            "outcome": "ok",
        }
        if len(evidence) >= MAX_EVIDENCE:
            errors.append(failure(["evidence"], "maxItems"))
        evidence = [*evidence[: MAX_EVIDENCE - 1], own]
    payload["evidence"] = evidence
    return payload, added, errors


def file_case(
    stores: HandoffStores, caller: Caller, arguments: dict[str, Any], now: datetime
) -> dict[str, Any]:
    given = arguments["payload"]
    handoff_id = given.get("handoff_id")
    if (
        given.get("customer_id") != caller.customer_id
        or given.get("session_id") != caller.origin_jti
    ):
        return refused("customer_mismatch")
    if not isinstance(handoff_id, str):
        return {
            "outcome": "invalid_input",
            "errors": [failure(["payload", "handoff_id"], "type")],
        }
    held = stores.cases.case(handoff_id)
    if held is not None and held["customer_id"] != caller.customer_id:
        return refused("customer_mismatch")
    if held is not None and held["status"] != "draft":
        return already_filed(held)
    payload, added, errors = completed(stores, caller, given, arguments["call_id"], now)
    try:
        payload, failures = checked(payload)
    except PayloadError as error:
        prefixed = [{**e, "path": f"/payload{e['path']}"[:256]} for e in error.errors]
        return {"outcome": "invalid_input", "errors": prefixed[:20]}
    errors += [e for e in failures if e not in errors]
    survived = [f for f in added if f in payload["verified_facts"]]
    entries = stores.records.turns(caller.origin_jti, arguments["turns"])
    payload, unrecorded = checked_against(
        payload, entries, arguments["call_id"], survived
    )
    errors += unrecorded
    if payload["priority"] == "normal" and urgent(payload):
        payload["priority"] = "urgent"
        errors.append(failure(["priority"], "policy"))
    filed_at = wall_time(now)
    case = {
        "pk": handoff_id,
        "kind": "case",
        "handoff_id": handoff_id,
        "customer_id": caller.customer_id,
        "payload": payload,
        "queue": payload["queue"],
        "priority": payload["priority"],
        "reason_code": payload["reason_code"],
        "language": payload["language"],
        "source": caller.source,
        "status": "filed",
        "queue_key": f"{caller.source}#{payload['queue']}#filed",
        "queue_order": queue_order(payload["priority"], filed_at),
        "filed_at": filed_at,
        "flagged": bool(errors),
        "validation_errors": errors[:MAX_ERRORS],
        "record": {"sign_in": caller.origin_jti, "turns": arguments["turns"]},
        "expires_at": expires(now),
    }
    if held is not None:
        case["saved_at"] = held["saved_at"]
    for _ in range(DRAWS):
        reference = draw_reference()
        drawn = {**case, "reference": reference}
        validator("handoff-case").validate(drawn)
        filed = stores.cases.file(
            drawn, reference_item(reference, handoff_id, case["expires_at"])
        )
        if filed == "filed":
            return {
                "outcome": "ok",
                "status": "filed",
                "handoff_id": handoff_id,
                "reference": reference,
                "queue": case["queue"],
                "priority": case["priority"],
                "added_facts": survived,
                "flagged": case["flagged"],
                "validation_errors": case["validation_errors"],
            }
        if filed == "case_taken":
            # Filed by another call between the read and the write.
            held = stores.cases.case(handoff_id)
            if held is None or held["customer_id"] != caller.customer_id:
                return refused("customer_mismatch")
            return already_filed(held)
    raise RuntimeError(f"{DRAWS} references drawn in a row were taken")


def file_handoff(
    stores: HandoffStores, caller: Caller, arguments: dict[str, Any], now: datetime
) -> dict[str, Any]:
    if (arguments["customer_id"], arguments["origin_jti"]) != (
        caller.customer_id,
        caller.origin_jti,
    ):
        return refused("customer_mismatch")
    if arguments["mode"] == "draft":
        return save_draft(stores, caller, arguments, now)
    return file_case(stores, caller, arguments, now)
