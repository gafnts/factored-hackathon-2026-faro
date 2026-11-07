"""
The staff allowlist's entries (ADR-0004, Operations, access controls, and its amendment of 2026-11-07): one Parameter
Store entry per staff user under the stack's staff_allowlist path, named by the user's sub, which Cognito never reuses,
and holding the username, so a listing reads. The pre-token trigger issues a staff token only to a user with one.
"""

from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError


class AllowlistError(Exception):
    pass


@dataclass(frozen=True)
class Allowlist:
    cognito: Any
    ssm: Any
    pool: str
    path: str

    def sub_of(self, username: str) -> str | None:
        try:
            user = self.cognito.admin_get_user(UserPoolId=self.pool, Username=username)
        except ClientError as error:
            if error.response["Error"]["Code"] == "UserNotFoundException":
                return None
            raise
        return str(
            next(a["Value"] for a in user["UserAttributes"] if a["Name"] == "sub")
        )

    def allow(self, username: str, sub: str | None = None) -> None:
        sub = sub or self.sub_of(username)
        if sub is None:
            raise AllowlistError("no such user in the pool")
        self.ssm.put_parameter(
            Name=self.path + sub, Value=username, Type="String", Overwrite=True
        )

    def disallow(self, username: str, sub: str | None = None) -> bool:
        """
        Whether there was an entry to take off; a user no longer in the pool has none to find by name.
        """
        sub = sub or self.sub_of(username)
        if sub is None:
            return False
        try:
            self.ssm.delete_parameter(Name=self.path + sub)
        except ClientError as error:
            if error.response["Error"]["Code"] == "ParameterNotFound":
                return False
            raise
        return True
