"""
The guest entry (ADR-0007, The guest entry, and its amendment of 2026-11-08), behind POST /api/guest with no
authorizer. Each click counts a guest against the day's cap, makes a user as a customer drawn from the curated list,
signs it in through the customers' app client, reads its cards through the Gateway with its new token for the welcome,
and returns the tokens with the welcome. It reads nothing from the request, so no input chooses the customer, the
groups, or the client. A full day and the pause are answers, a 200 that names the refusal, and no user is made.

It writes one line per click with its outcome alone, which the alarms read (ADR-0004, monitoring), and never a
username, a token, a customer's ID, or a name (SEC-03).
"""

import base64
import json
import logging
import os
import secrets
import string
import time
import urllib.request
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from typing import Any, Literal

import boto3
from botocore.exceptions import ClientError

from faro.contracts import validator
from faro.guest.customers import Customer, decode
from faro.guest.usage import admit

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Visitor first: a user the entry fails to finish is still one the cleanup deletes, and the trigger gives it no token
# until it's a customer too.
GROUPS = ("visitor", "customer")
ALPHABET = string.ascii_letters + string.digits
# The list changes only when make guest-list runs, so a warm function rereads it a few minutes later at most.
LIST_TTL_S = 300.0
CARDS_TIMEOUT_S = 5.0

Outcome = Literal["entered", "full", "paused", "failed"]
Cards = Callable[[str], list[dict[str, str]] | None]


@dataclass(frozen=True)
class Settings:
    pool: str
    client_id: str
    counters: str
    customers: str


def password() -> str:
    # The suffix meets every class the pool's policy requires, as the judges' and the harness's users do.
    return "".join(secrets.choice(ALPHABET) for _ in range(32)) + "aA1!"


def claims(token: str) -> dict[str, Any]:
    """
    A token Cognito has just issued us, read without checking its signature, which nothing it says depends on.
    """
    payload = token.split(".")[1]
    decoded: dict[str, Any] = json.loads(
        base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    )
    return decoded


def gateway_cards(url: str, target: str, timeout: float = CARDS_TIMEOUT_S) -> Cards:
    """
    list_cards as the agent calls it, with the visitor's own token, so the welcome names the cards the chat will.
    """
    if not url.startswith("https://"):
        raise ValueError("the Gateway's URL isn't https")

    def read(token: str) -> list[dict[str, str]] | None:
        token_claims = claims(token)
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": f"{target}___list_cards",
                "arguments": {
                    "customer_id": token_claims["customer_id"],
                    "origin_jti": token_claims["origin_jti"],
                },
            },
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode()
            streamed = response.headers.get("Content-Type", "").startswith(
                "text/event-stream"
            )
        if streamed:
            text = next(
                (
                    line[5:].strip()
                    for line in text.splitlines()
                    if line.startswith("data:")
                ),
                "",
            )
        output = json.loads(json.loads(text)["result"]["content"][0]["text"])
        if output.get("outcome") != "ok":
            return None
        return [
            {"product_type": c["product_type"], "last_four": c["last_four"]}
            for c in output["cards"]
        ]

    return read


@dataclass
class Entry:
    cognito: Any
    dynamodb: Any
    ssm: Any
    settings: Settings
    cards: Cards
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    clock: Callable[[], float] = time.monotonic
    choose: Callable[[Sequence[Customer]], Customer] = secrets.choice
    listed: tuple[tuple[Customer, ...], float] | None = field(default=None, repr=False)

    def customers(self) -> tuple[Customer, ...]:
        if self.listed is None or self.clock() - self.listed[1] > LIST_TTL_S:
            text = self.ssm.get_parameter(Name=self.settings.customers)["Parameter"][
                "Value"
            ]
            self.listed = (decode(text), self.clock())
        return self.listed[0]

    def make_user(self, customer: Customer) -> dict[str, str]:
        username, secret = f"visitor-{uuid.uuid4()}", password()
        pool = self.settings.pool
        self.cognito.admin_create_user(
            UserPoolId=pool,
            Username=username,
            UserAttributes=[
                {"Name": "custom:customer_id", "Value": customer.customer_id}
            ],
            MessageAction="SUPPRESS",
        )
        self.cognito.admin_add_user_to_group(
            UserPoolId=pool, Username=username, GroupName=GROUPS[0]
        )
        self.cognito.admin_set_user_password(
            UserPoolId=pool, Username=username, Password=secret, Permanent=True
        )
        self.cognito.admin_add_user_to_group(
            UserPoolId=pool, Username=username, GroupName=GROUPS[1]
        )
        signed = self.cognito.admin_initiate_auth(
            UserPoolId=pool,
            ClientId=self.settings.client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": secret},
        )
        tokens: dict[str, str] = signed["AuthenticationResult"]
        return tokens

    def welcome_cards(self, access_token: str) -> list[dict[str, str]] | None:
        try:
            return self.cards(access_token)
        except Exception as error:
            logger.warning(
                "the welcome's cards couldn't be read",
                extra={"error": type(error).__name__},
            )
            return None

    def enter(self) -> tuple[Outcome, dict[str, Any]]:
        admission = admit(self.dynamodb, self.settings.counters, self.now())
        if admission != "counted":
            return admission, {"refused": admission}
        customer = self.choose(self.customers())
        tokens = self.make_user(customer)
        return "entered", {
            "access_token": tokens["AccessToken"],
            "id_token": tokens["IdToken"],
            "refresh_token": tokens["RefreshToken"],
            "welcome": {
                "name": customer.name,
                "cards": self.welcome_cards(tokens["AccessToken"]),
            },
        }


def respond(entry: Callable[[], Entry]) -> dict[str, Any]:
    try:
        outcome, body = entry().enter()
        validator("guest", "entered" if outcome == "entered" else "refused").validate(
            body
        )
    except Exception as error:
        code = (
            error.response["Error"]["Code"] if isinstance(error, ClientError) else None
        )
        logger.error(
            "guest entry",
            extra={
                "metric": "guest_entry",
                "outcome": "failed",
                "error": type(error).__name__,
                "code": code,
            },
        )
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Cache-Control": "no-store",
            },
            "body": json.dumps({"message": "Internal Server Error"}),
        }
    logger.info("guest entry", extra={"metric": "guest_entry", "outcome": outcome})
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, ensure_ascii=False),
    }


@cache
def entry() -> Entry:
    return Entry(
        boto3.client("cognito-idp"),
        boto3.client("dynamodb"),
        boto3.client("ssm"),
        Settings(
            os.environ["USER_POOL_ID"],
            os.environ["CUSTOMER_CLIENT_ID"],
            os.environ["USAGE_COUNTERS_TABLE"],
            os.environ["GUEST_LIST_PARAMETER"],
        ),
        gateway_cards(os.environ["GATEWAY_URL"], os.environ["LIST_CARDS_TARGET"]),
    )


def handler(event: Any, context: Any) -> dict[str, Any]:
    return respond(entry)
