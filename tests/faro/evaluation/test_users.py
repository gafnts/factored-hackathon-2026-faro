"""
The harness's test users: one per case, named for the run and never for its customer, in the customer and evaluation
groups, refreshed near their end, and deleted; cleanup deletes only the evaluation group's users the harness named.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from botocore.exceptions import ClientError

from faro.evaluation import users
from faro.evaluation.player import unsigned

POOL, CLIENT = "us-east-1_pool", "client"


class Paginator:
    def __init__(self, cognito: "FakeCognito") -> None:
        self.cognito = cognito

    def paginate(self, **given: str) -> Iterator[dict[str, Any]]:
        assert given["UserPoolId"] == POOL
        group = given["GroupName"]
        names = [n for n, u in self.cognito.users.items() if group in u["groups"]]
        # Two pages, as Cognito pages a group's users.
        yield {"Users": [{"Username": n} for n in names[:1]]}
        yield {"Users": [{"Username": n} for n in names[1:]]}


class FakeCognito:
    def __init__(self, expires: int = 4_000_000_000) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.expires = expires
        self.fail_group = False
        self.refreshed = 0

    def admin_create_user(self, **given: Any) -> None:
        assert given["UserPoolId"] == POOL and given["MessageAction"] == "SUPPRESS"
        self.users[given["Username"]] = {
            "attributes": {a["Name"]: a["Value"] for a in given["UserAttributes"]},
            "groups": [],
        }

    def admin_set_user_password(self, **given: Any) -> None:
        assert given["Permanent"] is True
        self.users[given["Username"]]["password"] = given["Password"]

    def admin_add_user_to_group(self, **given: Any) -> None:
        if self.fail_group:
            raise ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "add")
        self.users[given["Username"]]["groups"].append(given["GroupName"])

    def admin_initiate_auth(self, **given: Any) -> dict[str, Any]:
        assert (given["UserPoolId"], given["ClientId"]) == (POOL, CLIENT)
        parameters = given["AuthParameters"]
        if given["AuthFlow"] == "REFRESH_TOKEN_AUTH":
            self.refreshed += 1
            assert parameters == {"REFRESH_TOKEN": "refresh"}
        else:
            user = self.users[parameters["USERNAME"]]
            assert parameters["PASSWORD"] == user["password"]
        claims = {"origin_jti": "sign-in", "exp": self.expires + self.refreshed}
        return {
            "AuthenticationResult": {
                "AccessToken": unsigned(claims),
                "RefreshToken": "refresh",
            }
        }

    def admin_delete_user(self, **given: Any) -> None:
        if given["Username"] not in self.users:
            raise ClientError({"Error": {"Code": "UserNotFoundException"}}, "delete")
        del self.users[given["Username"]]

    def get_paginator(self, name: str) -> Paginator:
        assert name == "list_users_in_group"
        return Paginator(self)


def made(cognito: FakeCognito, now: float = 0) -> users.Users:
    return users.Users(cognito, POOL, CLIENT, now=lambda: now)  # type: ignore[arg-type]


def test_a_case_gets_a_user_named_for_the_run_with_its_customer_set_by_an_admin() -> (
    None
):
    cognito = FakeCognito()

    user = made(cognito).create(users.username("20261001T190000Z-ab12", 7), "CLI-1")

    assert user.username == "eval-20261001T190000Z-ab12-0007"
    held = cognito.users[user.username]
    assert held["attributes"] == {"custom:customer_id": "CLI-1"}
    assert held["groups"] == ["customer", "evaluation"]
    assert user.password not in repr(user)


def test_a_user_that_cant_be_put_in_its_groups_is_deleted() -> None:
    cognito = FakeCognito()
    cognito.fail_group = True

    with pytest.raises(ClientError):
        made(cognito).create("eval-run-0001", "CLI-1")

    assert cognito.users == {}


def test_a_user_signs_in_and_its_token_is_refreshed_only_near_its_end() -> None:
    cognito = FakeCognito(expires=1_000)
    harness = made(cognito, now=500)
    signed = harness.sign_in(harness.create("eval-run-0001", "CLI-1"))

    assert signed.origin_jti == "sign-in"
    assert harness.fresh(signed) == signed.access
    assert cognito.refreshed == 0

    late = made(cognito, now=1_000 - users.MARGIN + 1)
    before = signed.access
    assert late.fresh(signed) != before
    assert (cognito.refreshed, signed.origin_jti) == (1, "sign-in")
    assert "refresh" not in repr(signed)


def test_deleting_a_user_already_gone_is_fine() -> None:
    cognito = FakeCognito()
    harness = made(cognito)
    user = harness.create("eval-run-0001", "CLI-1")

    harness.delete(user)
    harness.delete(user)

    assert cognito.users == {}


def test_cleanup_deletes_only_the_evaluation_groups_users_the_harness_named() -> None:
    cognito = FakeCognito()
    harness = made(cognito)
    for name in ("eval-a-0001", "eval-a-0002", "eval-b-0001"):
        harness.create(name, "CLI-1")
    cognito.users["it-run-evaluation"] = {"attributes": {}, "groups": ["evaluation"]}
    cognito.users["eval-a-0003"] = {"attributes": {}, "groups": ["customer"]}

    assert harness.cleanup("a") == 2
    assert sorted(cognito.users) == ["eval-a-0003", "eval-b-0001", "it-run-evaluation"]
    assert harness.cleanup() == 1
    assert sorted(cognito.users) == ["eval-a-0003", "it-run-evaluation"]
