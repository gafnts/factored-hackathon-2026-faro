"""
The pre-token trigger, run at every sign-in and every refresh. It refuses a token when the app client doesn't match the
user's group, since any user of a pool can sign in through any of its clients and both client IDs are public (ADR-0007,
Sign-in), and it copies the admin-written custom:customer_id into a customer's access token, where the Runtime and the
Gateway read it (POL-07).
"""

import logging
import os
from collections.abc import Iterable
from typing import Any, Literal

Side = Literal["customers", "staff"]

CUSTOMER_GROUPS = frozenset({"customer"})
STAFF_GROUPS = frozenset({"human_agent", "ai_team"})

logger = logging.getLogger(__name__)


class SignInRefusedError(Exception):
    """
    Cognito refuses the sign-in and passes the message to the caller, so it says nothing about why.
    """

    def __init__(self) -> None:
        super().__init__("Sign-in refused.")


def side_of(groups: Iterable[str]) -> Side | None:
    names = set(groups)
    customer, staff = bool(names & CUSTOMER_GROUPS), bool(names & STAFF_GROUPS)
    if customer == staff:
        return None
    return "customers" if customer else "staff"


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    clients: dict[str, Side] = {
        os.environ["CUSTOMER_CLIENT_ID"]: "customers",
        os.environ["STAFF_CLIENT_ID"]: "staff",
    }
    client = clients.get(event["callerContext"]["clientId"])
    groups = (
        event["request"].get("groupConfiguration", {}).get("groupsToOverride") or []
    )
    user = side_of(groups)
    if client is None or user != client:
        logger.warning(
            "refused %s: user %s is on %s, client on %s",
            event["triggerSource"],
            event["userName"],
            user or "no side",
            client or "no side",
        )
        raise SignInRefusedError()

    customer_id = event["request"]["userAttributes"].get("custom:customer_id")
    if user == "customers" and customer_id:
        event["response"]["claimsAndScopeOverrideDetails"] = {
            "accessTokenGeneration": {
                "claimsToAddOrOverride": {"customer_id": customer_id}
            }
        }
    return event
