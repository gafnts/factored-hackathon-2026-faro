"""
The harness's test users (ADR-0005, Running a case; decision 8): one per case, named for the run and the case's place in
it, never for its customer, with custom:customer_id written by an admin and membership in the customer and evaluation
groups, so the pre-token trigger adds the customer's claims and the record says source=evaluation. A password and a
token never leave memory. A user is deleted once its case ends; cleanup deletes what a stopped run left behind, and only
users in the evaluation group named as the harness names them, since IAM scopes the role to the pool, not to a group.
"""

import secrets
import string
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from faro.agent.claims import CUSTOMER_GROUP, EVALUATION_GROUP
from faro.tools.identity import claims_of

if TYPE_CHECKING:
    from mypy_boto3_cognito_idp import CognitoIdentityProviderClient

PREFIX = "eval-"
# A token this close to its end is refreshed before a turn is sent, so no turn meets an expired one.
MARGIN = 120


def username(run: str, n: int) -> str:
    return f"{PREFIX}{run}-{n:04d}"


@dataclass(frozen=True)
class User:
    username: str
    password: str = field(repr=False)


@dataclass
class SignedIn:
    """
    One sign-in of a test user. A refresh keeps its origin_jti, so the sign-in stays one.
    """

    access: str = field(repr=False)
    refresh: str = field(repr=False)

    @property
    def origin_jti(self) -> str:
        found: str = claims_of(self.access)["origin_jti"]
        return found

    @property
    def expires(self) -> int:
        found: int = claims_of(self.access)["exp"]
        return found


class Users:
    def __init__(
        self,
        cognito: "CognitoIdentityProviderClient",
        pool: str,
        client_id: str,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.cognito = cognito
        self.pool = pool
        self.client_id = client_id
        self.now = now

    def create(self, name: str, customer_id: str) -> User:
        alphabet = string.ascii_letters + string.digits
        user = User(name, "".join(secrets.choice(alphabet) for _ in range(24)) + "aA1!")
        self.cognito.admin_create_user(
            UserPoolId=self.pool,
            Username=user.username,
            UserAttributes=[{"Name": "custom:customer_id", "Value": customer_id}],
            MessageAction="SUPPRESS",
        )
        try:
            self.cognito.admin_set_user_password(
                UserPoolId=self.pool,
                Username=user.username,
                Password=user.password,
                Permanent=True,
            )
            for group in (CUSTOMER_GROUP, EVALUATION_GROUP):
                self.cognito.admin_add_user_to_group(
                    UserPoolId=self.pool, Username=user.username, GroupName=group
                )
        except Exception:
            self.delete(user)
            raise
        return user

    def sign_in(self, user: User) -> SignedIn:
        result = self._auth(
            "ADMIN_USER_PASSWORD_AUTH",
            {"USERNAME": user.username, "PASSWORD": user.password},
        )
        return SignedIn(result["AccessToken"], result["RefreshToken"])

    def fresh(self, signed: SignedIn) -> str:
        """
        The sign-in's access token, refreshed first when it's near its end.
        """
        if signed.expires - self.now() < MARGIN:
            result = self._auth("REFRESH_TOKEN_AUTH", {"REFRESH_TOKEN": signed.refresh})
            signed.access = result["AccessToken"]
        return signed.access

    def delete(self, user: User) -> None:
        try:
            self.cognito.admin_delete_user(UserPoolId=self.pool, Username=user.username)
        except ClientError as error:
            if error.response["Error"]["Code"] != "UserNotFoundException":
                raise

    def cleanup(self, run: str | None = None) -> int:
        """
        Deletes the evaluation group's users the harness named, a run's only when run is given, and counts them.
        """
        prefix = PREFIX if run is None else f"{PREFIX}{run}-"
        found = [
            u["Username"]
            for page in self.cognito.get_paginator("list_users_in_group").paginate(
                UserPoolId=self.pool, GroupName=EVALUATION_GROUP
            )
            for u in page["Users"]
            if u["Username"].startswith(prefix)
        ]
        for name in found:
            self.delete(User(name, ""))
        return len(found)

    def _auth(self, flow: Any, parameters: dict[str, str]) -> Any:
        return self.cognito.admin_initiate_auth(
            UserPoolId=self.pool,
            ClientId=self.client_id,
            AuthFlow=flow,
            AuthParameters=parameters,
        )["AuthenticationResult"]
