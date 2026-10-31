"""
Who is filing a handoff (ADR-0004, Where the tools run, and its amendment of 2026-09-30; POL-07, POL-08, SEC-04).
file_handoff is off the Gateway, so no authorizer or Cedar has checked the token the Runtime forwards. Cognito's GetUser
answers only a live access token of the pool's, refusing a forged, expired, or revoked one, and returns the user's
customer_id; the token's claims, then known to be Cognito's own, must name the customers' app client, the access use,
the customer group, the same user, and the same customer.
"""

import base64
import json
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_cognito_idp import CognitoIdentityProviderClient

CUSTOMER_ID = re.compile(r"^CLI-[A-Z0-9]{12}$")
CUSTOMER_GROUP = "customer"
EVALUATION_GROUP = "evaluation"
# What GetUser answers a token it won't honor with; anything else is a fault, not a refusal.
REFUSED = (
    "NotAuthorizedException",
    "UserNotFoundException",
    "UserNotConfirmedException",
    "PasswordResetRequiredException",
    "InvalidParameterException",
)


@dataclass(frozen=True)
class Caller:
    sub: str
    customer_id: str
    origin_jti: str
    source: str


class Verifier(Protocol):
    def verify(self, token: str) -> Caller | None: ...


def claims_of(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        decoded = json.loads(
            base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        )
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if str(uuid.UUID(value)) == value else None
    except ValueError:
        return None


def caller_of(token: str, user: dict[str, Any], client_id: str) -> Caller | None:
    """
    The caller a token names, once Cognito has answered GetUser with it, or None when the token isn't a customer's.
    """
    claims = claims_of(token)
    attributes = {a["Name"]: a["Value"] for a in user.get("UserAttributes", [])}
    groups = claims.get("cognito:groups")
    sub, origin_jti = _uuid(claims.get("sub")), _uuid(claims.get("origin_jti"))
    customer_id = claims.get("customer_id")
    if (
        sub is None
        or origin_jti is None
        or sub != attributes.get("sub")
        or claims.get("token_use") != "access"
        or claims.get("client_id") != client_id
        or not isinstance(groups, list)
        or CUSTOMER_GROUP not in groups
        or not isinstance(customer_id, str)
        or not CUSTOMER_ID.match(customer_id)
        or customer_id != attributes.get("custom:customer_id")
    ):
        return None
    source = "evaluation" if EVALUATION_GROUP in groups else "demo"
    return Caller(sub, customer_id, origin_jti, source)


class CognitoVerifier:
    def __init__(self, client: "CognitoIdentityProviderClient", client_id: str) -> None:
        self._client = client
        self._client_id = client_id

    def verify(self, token: str) -> Caller | None:
        if not token or not claims_of(token):
            return None
        try:
            user = self._client.get_user(AccessToken=token)
        except ClientError as error:
            if error.response["Error"]["Code"] in REFUSED:
                return None
            raise
        return caller_of(token, dict(user), self._client_id)
