"""
The pre-token trigger, run at every sign-in and every refresh. It refuses a token when the app client doesn't match the
user's group, since any user of a pool can sign in through any of its clients and both client IDs are public (ADR-0007,
Sign-in), and it copies the admin-written custom:customer_id into a customer's access token, where the Runtime and the
Gateway read it (POL-07).

The staff client also refuses a visitor, and issues a token only to a user on the staff allowlist: a Parameter Store
entry named by the user's sub, which the guest entry's role can't write. IAM holds Cognito's administrator actions to
the pool, not to a group, so a guest entry whose code is wrong could put a user in a staff group; the allowlist keeps
that user from a staff token (ADR-0004, Operations, access controls, and its amendment of 2026-11-07).
"""

import functools
import logging
import os
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Literal

import boto3
from botocore.exceptions import BotoCoreError, ClientError

if TYPE_CHECKING:
    from mypy_boto3_ssm import SSMClient

Side = Literal["customers", "staff"]

CUSTOMER_GROUPS = frozenset({"customer"})
STAFF_GROUPS = frozenset({"human_agent", "ai_team"})
VISITOR_GROUP = "visitor"

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


@functools.cache
def parameters() -> "SSMClient":
    return boto3.client("ssm")


def listed(sub: str) -> bool:
    """
    Read only when the staff client is asked, so a customer's sign-in doesn't wait for it. Anything but the entry
    counts as no entry, so a fault refuses rather than admits.
    """
    try:
        parameters().get_parameter(Name=os.environ["STAFF_ALLOWLIST"] + sub)
    except ClientError as error:
        if error.response["Error"]["Code"] != "ParameterNotFound":
            logger.exception("the staff allowlist couldn't be read")
        return False
    except BotoCoreError:
        logger.exception("the staff allowlist couldn't be read")
        return False
    return True


def refuse(event: dict[str, Any], why: str) -> SignInRefusedError:
    logger.warning(
        "refused %s: user %s %s", event["triggerSource"], event["userName"], why
    )
    return SignInRefusedError()


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
        raise refuse(
            event, f"is on {user or 'no side'}, client on {client or 'no side'}"
        )
    if user == "staff":
        if VISITOR_GROUP in groups:
            raise refuse(event, "is a visitor")
        if not listed(event["request"]["userAttributes"]["sub"]):
            raise refuse(event, "isn't on the staff allowlist")

    customer_id = event["request"]["userAttributes"].get("custom:customer_id")
    if user == "customers" and customer_id:
        event["response"]["claimsAndScopeOverrideDetails"] = {
            "accessTokenGeneration": {
                "claimsToAddOrOverride": {"customer_id": customer_id}
            }
        }
    return event
