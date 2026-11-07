"""
The staff allowlist's script: one named staff user on or off, and the one-off labeling of the users made before the
labels and the allowlist, which never trusts a pool's staff wholesale once it holds a visitor and prints counts alone
(ADR-0004's amendment of 2026-11-07; SEC-03).
"""

import json
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from faro import staff
from faro.allowlist import Allowlist, AllowlistError

ALLOWLIST = "/banking-agent-local/staff/"


class Pages:
    def __init__(self, cognito: "Cognito") -> None:
        self.cognito = cognito

    def paginate(self, **kwargs: str) -> list[dict[str, Any]]:
        users = [
            {"Username": name, "Attributes": [{"Name": "sub", "Value": f"sub-{name}"}]}
            for name, groups in self.cognito.users.items()
            if kwargs["GroupName"] in groups
        ]
        # Two pages, so a listing that read only the first would miss users.
        return [{"Users": users[:1]}, {"Users": users[1:]}]


class Cognito:
    def __init__(self, users: dict[str, set[str]]) -> None:
        self.users = users
        self.added: list[tuple[str, str]] = []

    def get_paginator(self, name: str) -> Pages:
        assert name == "list_users_in_group"
        return Pages(self)

    def admin_get_user(self, **kwargs: str) -> dict[str, Any]:
        username = kwargs["Username"]
        if username not in self.users:
            raise ClientError(
                {"Error": {"Code": "UserNotFoundException", "Message": ""}},
                "AdminGetUser",
            )
        return {"UserAttributes": [{"Name": "sub", "Value": f"sub-{username}"}]}

    def admin_list_groups_for_user(self, **kwargs: str) -> dict[str, Any]:
        groups = sorted(self.users[kwargs["Username"]])
        return {"Groups": [{"GroupName": g} for g in groups]}

    def admin_add_user_to_group(self, **kwargs: str) -> None:
        self.users[kwargs["Username"]].add(kwargs["GroupName"])
        self.added.append((kwargs["Username"], kwargs["GroupName"]))


class SSM:
    def __init__(self, entries: dict[str, str] | None = None) -> None:
        self.entries = dict(entries or {})

    def put_parameter(self, **kwargs: Any) -> None:
        assert kwargs["Type"] == "String"
        self.entries[kwargs["Name"]] = kwargs["Value"]

    def delete_parameter(self, **kwargs: str) -> None:
        if self.entries.pop(kwargs["Name"], None) is None:
            raise ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": ""}},
                "DeleteParameter",
            )


def pool() -> dict[str, set[str]]:
    return {
        "team.quiet": {"customer"},
        "team.mixed": {"customer", "invited"},
        "eval-run-0001": {"customer", "evaluation"},
        "smoke-check": {"customer"},
        "agente": {"human_agent"},
        "equipo-ia": {"ai_team"},
    }


def allowlist(cognito: Cognito, ssm: SSM) -> Allowlist:
    return Allowlist(cognito, ssm, "pool", ALLOWLIST)


def test_labeling_labels_the_unlabeled_customers_moves_the_smoke_user_and_lists_the_staff() -> (
    None
):
    cognito, ssm = Cognito(pool()), SSM()

    counts = staff.label_users(allowlist(cognito, ssm), dry_run=False)

    assert counts == {"invited": 1, "smoke": 1, "staff": 2}
    assert sorted(cognito.added) == [
        ("smoke-check", "evaluation"),
        ("team.quiet", "invited"),
    ]
    assert ssm.entries == {
        f"{ALLOWLIST}sub-agente": "agente",
        f"{ALLOWLIST}sub-equipo-ia": "equipo-ia",
    }


def test_a_dry_labeling_counts_and_changes_nothing() -> None:
    cognito, ssm = Cognito(pool()), SSM()

    counts = staff.label_users(allowlist(cognito, ssm), dry_run=True)

    assert counts == {"invited": 1, "smoke": 1, "staff": 2}
    assert cognito.added == []
    assert ssm.entries == {}


def test_labeling_runs_again_without_changing_anything() -> None:
    cognito, ssm = Cognito(pool()), SSM()
    staff.label_users(allowlist(cognito, ssm), dry_run=False)
    cognito.added.clear()

    counts = staff.label_users(allowlist(cognito, ssm), dry_run=False)

    assert (counts["invited"], counts["smoke"], cognito.added) == (0, 0, [])


def test_labeling_refuses_a_pool_that_holds_a_visitor() -> None:
    users = pool() | {"guest-0001": {"customer", "visitor"}}
    cognito, ssm = Cognito(users), SSM()

    with pytest.raises(staff.StaffError, match="make staff"):
        staff.label_users(allowlist(cognito, ssm), dry_run=False)

    assert (cognito.added, ssm.entries) == ([], {})


def test_add_puts_a_staff_user_on_by_its_sub_and_remove_takes_it_off() -> None:
    cognito, ssm = Cognito(pool()), SSM()

    staff.add(allowlist(cognito, ssm), "agente")
    assert ssm.entries == {f"{ALLOWLIST}sub-agente": "agente"}

    assert allowlist(cognito, ssm).disallow("agente") is True
    assert ssm.entries == {}
    assert allowlist(cognito, ssm).disallow("agente") is False


@pytest.mark.parametrize("username", ["team.quiet", "guest-0001"])
def test_add_refuses_a_customer_and_a_visitor_in_a_staff_group(username: str) -> None:
    users = pool() | {"guest-0001": {"visitor", "human_agent"}}
    cognito, ssm = Cognito(users), SSM()

    with pytest.raises(staff.StaffError, match="staff user"):
        staff.add(allowlist(cognito, ssm), username)

    assert ssm.entries == {}


def test_a_user_not_in_the_pool_can_be_neither_added_nor_found() -> None:
    cognito, ssm = Cognito(pool()), SSM()

    with pytest.raises(AllowlistError):
        allowlist(cognito, ssm).allow("nobody")
    assert allowlist(cognito, ssm).disallow("nobody") is False


def stack(tmp_path: Path, **outputs: str) -> Path:
    path = tmp_path / "local.outputs.json"
    path.write_text(json.dumps({k: {"value": v} for k, v in outputs.items()}))
    return path


def test_run_prints_counts_and_never_a_username(tmp_path: Path) -> None:
    path = stack(tmp_path, user_pool_id="pool", staff_allowlist=ALLOWLIST)
    args = staff.parse(["label-users", "--stack", str(path), "--dry-run"])

    said = staff.run(args, Cognito(pool()), SSM())

    assert said == (
        "would label 1 customers invited and 1 smoke user evaluation, "
        "and would put 2 staff users on the allowlist"
    )
    assert not any(name in said for name in pool())


def test_a_stack_without_the_allowlist_is_refused(tmp_path: Path) -> None:
    args = staff.parse(
        ["label-users", "--stack", str(stack(tmp_path, user_pool_id="pool"))]
    )

    with pytest.raises(staff.StaffError, match="staff_allowlist"):
        staff.run(args, Cognito(pool()), SSM())


def test_adding_or_removing_needs_the_user() -> None:
    for command in ("add", "remove"):
        with pytest.raises(SystemExit):
            staff.parse([command, "--stack", "build/local.outputs.json"])
