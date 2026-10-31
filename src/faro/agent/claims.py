"""
Reads the claims of the access token the Runtime's authorizer already validated (ADR-0004, A turn, end to end). Only the
authorizer reaches the entrypoint, so the signature isn't checked again; what a misconfigured authorizer would let
through is: a token of another use or client, or outside the customer group. customer_id comes only from the
admin-written claim, and a token without it is refused (POL-07).
"""

import base64
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

CUSTOMER_ID = re.compile(r"^CLI-[A-Z0-9]{12}$")
CUSTOMER_GROUP = "customer"
EVALUATION_GROUP = "evaluation"


@dataclass(frozen=True)
class Claims:
    sub: str
    customer_id: str
    origin_jti: str
    groups: tuple[str, ...]

    @property
    def source(self) -> str:
        return source_of(self.groups)


class ClaimsRefusedError(Exception):
    """
    code is no_customer or internal; sub and origin_jti are kept when they could be read, so the refusal can be recorded.
    """

    def __init__(
        self, code: str, sub: str | None, origin_jti: str | None, source: str
    ) -> None:
        super().__init__(code)
        self.code = code
        self.sub = sub
        self.origin_jti = origin_jti
        self.source = source


def source_of(groups: Any) -> str:
    listed = groups if isinstance(groups, list | tuple) else []
    return "evaluation" if EVALUATION_GROUP in listed else "demo"


def uuid_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value)) if str(uuid.UUID(value)) == value else None
    except ValueError:
        return None


def payload(authorization: str | None) -> dict[str, Any]:
    scheme, _, token = (authorization or "").partition(" ")
    parts = token.split(".")
    if scheme.lower() != "bearer" or len(parts) != 3:
        return {}
    try:
        decoded = json.loads(
            base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        )
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def read_claims(authorization: str | None, client_id: str) -> Claims:
    token = payload(authorization)
    sub = uuid_or_none(token.get("sub"))
    origin_jti = uuid_or_none(token.get("origin_jti"))
    groups = token.get("cognito:groups", [])
    if (
        sub is None
        or origin_jti is None
        or token.get("token_use") != "access"
        or token.get("client_id") != client_id
        or not isinstance(groups, list)
    ):
        raise ClaimsRefusedError("internal", sub, origin_jti, source_of(groups))
    customer_id = token.get("customer_id")
    if (
        not isinstance(customer_id, str)
        or not CUSTOMER_ID.match(customer_id)
        or CUSTOMER_GROUP not in groups
    ):
        raise ClaimsRefusedError("no_customer", sub, origin_jti, source_of(groups))
    return Claims(sub, customer_id, origin_jti, tuple(str(g) for g in groups))


def token_of(authorization: str | None) -> str:
    return (authorization or "").partition(" ")[2]
