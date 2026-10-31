"""
Throwaway users in the deployed stack's pool, signed in through IAM and deleted afterwards. The stack comes from the
Terraform outputs that make integration writes; passwords and tokens stay in memory and are never printed. The two
customers are the journeys' personas (make personas), so their cards are in the tools' data; their IDs come from
data/personas/ and are never printed either. The probe (make probe) makes its users the same way. The cases a test
saves or files are deleted afterwards too, each with its reference.
"""

import base64
import contextlib
import json
import os
import secrets
import string
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from mypy_boto3_cognito_idp import CognitoIdentityProviderClient
from mypy_boto3_cognito_idp.type_defs import AttributeTypeTypeDef

from faro import personas
from faro.dataset.lock import read_lock
from faro.tools.cases import REFERENCE, plain

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class User:
    username: str
    password: str
    customer_id: str | None


SignIn = Callable[[User, str], dict[str, str]]


def claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    decoded: dict[str, Any] = json.loads(
        base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    )
    return decoded


@pytest.fixture(scope="session")
def outputs() -> dict[str, Any]:
    path = os.environ.get("STACK_OUTPUTS")
    if not path or not Path(path).is_file():
        pytest.skip("no stack outputs; run make integration")
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: value["value"] for name, value in raw.items()}


@pytest.fixture(scope="session")
def cognito() -> CognitoIdentityProviderClient:
    return boto3.client("cognito-idp", region_name="us-east-1")


@pytest.fixture(scope="session")
def persona_ids() -> dict[str, str]:
    snapshot = read_lock(ROOT / "dataset.lock").snapshot_id
    try:
        chosen = personas.read(personas.path_for(ROOT / "data", snapshot), snapshot)
    except personas.PersonaError as error:
        pytest.fail(str(error))
    return {scenario: p.customer_id for scenario, p in chosen.items()}


@contextlib.contextmanager
def throwaway_users(
    pool: str,
    cognito: CognitoIdentityProviderClient,
    wanted: dict[str, tuple[list[str], str | None]],
) -> Iterator[dict[str, User]]:
    run = uuid.uuid4().hex[:8]
    alphabet = string.ascii_letters + string.digits
    created: dict[str, User] = {}
    try:
        for role, (groups, customer_id) in wanted.items():
            user = User(
                f"it-{run}-{role.replace('_', '-')}",
                "".join(secrets.choice(alphabet) for _ in range(24)) + "aA1!",
                customer_id,
            )
            attributes: list[AttributeTypeTypeDef] = (
                [{"Name": "custom:customer_id", "Value": customer_id}]
                if customer_id
                else []
            )
            cognito.admin_create_user(
                UserPoolId=pool,
                Username=user.username,
                UserAttributes=attributes,
                MessageAction="SUPPRESS",
            )
            created[role] = user
            cognito.admin_set_user_password(
                UserPoolId=pool,
                Username=user.username,
                Password=user.password,
                Permanent=True,
            )
            for group in groups:
                cognito.admin_add_user_to_group(
                    UserPoolId=pool, Username=user.username, GroupName=group
                )
        yield created
    finally:
        left: dict[str, str] = {}
        for user in created.values():
            try:
                cognito.admin_delete_user(UserPoolId=pool, Username=user.username)
            except ClientError as error:
                code = error.response["Error"]["Code"]
                if code != "UserNotFoundException":
                    left[user.username] = code
        # Every delete is tried before the misses are raised, so one failure strands nothing else.
        if left:
            raise RuntimeError(
                "users left in the pool, delete them by hand: "
                + ", ".join(f"{name} ({code})" for name, code in sorted(left.items()))
            )


def signed_in(
    outputs: dict[str, Any],
    cognito: CognitoIdentityProviderClient,
    user: User,
    client: str,
) -> dict[str, str]:
    result = cognito.admin_initiate_auth(
        UserPoolId=outputs["user_pool_id"],
        ClientId=outputs[f"{client}_client_id"],
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": user.username, "PASSWORD": user.password},
    )["AuthenticationResult"]
    return {
        "access": result["AccessToken"],
        "id": result["IdToken"],
        "refresh": result["RefreshToken"],
    }


@pytest.fixture(scope="session")
def users(
    outputs: dict[str, Any],
    cognito: CognitoIdentityProviderClient,
    persona_ids: dict[str, str],
) -> Iterator[dict[str, User]]:
    wanted: dict[str, tuple[list[str], str | None]] = {
        # The journeys' two personas, whose records stage the suite's paths (ADR-0007, Judges' access).
        "customer": (["customer"], persona_ids["declines"]),
        "other_customer": (["customer"], persona_ids["dispute"]),
        # Not in the tools' data.
        "unknown_customer": (["customer"], "CLI-ITEST0000001"),
        "staff": (["human_agent"], None),
        "ai_team": (["ai_team"], None),
        "both_staff": (["human_agent", "ai_team"], None),
        # A customer the evaluation's harness would sign in, whose cases never reach a human agent (EVL-13).
        "evaluation": (["customer", "evaluation"], persona_ids["declines"]),
        # Its day's turns are filled to the cap, so no other test signs it in (decision 21).
        "capped": (["customer", "evaluation"], persona_ids["declines"]),
        "no_group": ([], "CLI-ITEST0000003"),
        "no_claim": (["customer"], None),
    }
    with throwaway_users(outputs["user_pool_id"], cognito, wanted) as created:
        yield created


@pytest.fixture(scope="session")
def sign_in(outputs: dict[str, Any], cognito: CognitoIdentityProviderClient) -> SignIn:
    def signed(user: User, client: str) -> dict[str, str]:
        return signed_in(outputs, cognito, user, client)

    return signed


def cases(outputs: dict[str, Any]) -> Any:
    return boto3.resource("dynamodb", region_name="us-east-1").Table(
        outputs["handoff"]["cases_table"]
    )


def stored(outputs: dict[str, Any], handoff_id: str) -> dict[str, Any] | None:
    item = cases(outputs).get_item(Key={"pk": handoff_id}, ConsistentRead=True)
    found: dict[str, Any] | None = plain(item.get("Item"))
    return found


def handoffs(entries: list[dict[str, Any]]) -> list[str]:
    """
    Every draft and case a sign-in's turns saved or filed, by handoff ID.
    """
    named = [
        e["result"]["handoff_id"]
        for e in entries
        if e["kind"] == "tool_call"
        and e["tool"] == "file_handoff"
        and "handoff_id" in (e.get("result") or {})
    ]
    named += [e["handoff_id"] for e in entries if e["kind"] == "handoff"]
    return list(dict.fromkeys(named))


@pytest.fixture
def saved(outputs: dict[str, Any]) -> Iterator[list[str]]:
    """
    The handoff IDs a test saves or files, deleted afterwards with their references.
    """
    handoff_ids: list[str] = []
    yield handoff_ids
    for handoff_id in dict.fromkeys(handoff_ids):
        case = stored(outputs, handoff_id)
        if case is not None and "reference" in case:
            cases(outputs).delete_item(Key={"pk": f"{REFERENCE}{case['reference']}"})
        cases(outputs).delete_item(Key={"pk": handoff_id})
