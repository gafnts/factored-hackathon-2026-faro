"""
Who is calling the console API (ADR-0007, The console API, and its amendments of 2026-09-30; SEC-05, CTL-04). API
Gateway's JWT authorizer has checked the token's signature, issuer, expiry, audience, and scope before any of this runs,
and hands its claims to the Lambda. Each route then requires an access token of the staff app client in the human_agent
group, so a misconfigured authorizer still lets no customer, and no one on the AI team, read a case.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

HUMAN_AGENT = "human_agent"


@dataclass(frozen=True)
class Staff:
    sub: str


def groups_of(claim: Any) -> list[str] | None:
    """
    An HTTP API is reported to hand a Lambda a list claim as one string, the groups between brackets and apart by
    spaces (ADR-0004, To verify on the first deploy); a list is read too, and anything else is no groups at all. A
    group's name can hold a comma but no space, so a group named "x,human_agent" is never read as human_agent.
    """
    if isinstance(claim, list) and all(isinstance(group, str) for group in claim):
        return claim
    if isinstance(claim, str) and claim.startswith("[") and claim.endswith("]"):
        return claim[1:-1].split()
    return None


def claims_of(event: Any) -> Mapping[str, Any]:
    found: Any = event
    for key in ("requestContext", "authorizer", "jwt", "claims"):
        found = found.get(key) if isinstance(found, Mapping) else None
    return found if isinstance(found, Mapping) else {}


def _uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if str(uuid.UUID(value)) == value else None
    except ValueError:
        return None


def staff_of(event: Any, client_id: str) -> Staff | None:
    """
    The human agent an event's claims name, or None when they aren't an access token of the staff client in the group.
    """
    claims = claims_of(event)
    sub = _uuid(claims.get("sub"))
    groups = groups_of(claims.get("cognito:groups"))
    if (
        sub is None
        or claims.get("token_use") != "access"
        or claims.get("client_id") != client_id
        or groups is None
        or HUMAN_AGENT not in groups
    ):
        return None
    return Staff(sub)
