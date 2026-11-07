"""
The staff allowlist (ADR-0004, Operations, access controls, and its amendment of 2026-11-07): the pre-token trigger
issues a staff token only to a user with an entry under the stack's staff_allowlist path, one Parameter Store entry per
user named by its sub, which Cognito never reuses. make judges puts the staff it makes on it, and the suites their own;
this script puts one named staff user on it or takes one off.

label-users runs once per environment, for the users made before the labels and the allowlist: it labels every customer
with no label invited, moves the smoke user to evaluation, and puts every staff user on the allowlist. That last is
safe only while no visitor exists, since a guest entry gone wrong could have made staff, so it refuses a pool that
holds one; it goes before the guest entry is built. It prints counts, never a customer's username or an ID (SEC-03).
"""

import argparse
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from faro import judges, smoke_user
from faro.allowlist import Allowlist, AllowlistError

STAFF_GROUPS = ("human_agent", "ai_team")
LABELS = ("invited", "evaluation", "visitor")


class StaffError(Exception):
    pass


def members(cognito: Any, pool: str, group: str) -> Iterator[dict[str, Any]]:
    pages = cognito.get_paginator("list_users_in_group")
    for page in pages.paginate(UserPoolId=pool, GroupName=group):
        yield from page["Users"]


def sub_in(user: dict[str, Any]) -> str:
    return str(next(a["Value"] for a in user["Attributes"] if a["Name"] == "sub"))


def add(allowlist: Allowlist, username: str) -> None:
    groups = {
        g["GroupName"]
        for g in allowlist.cognito.admin_list_groups_for_user(
            UserPoolId=allowlist.pool, Username=username
        )["Groups"]
    }
    if not groups & set(STAFF_GROUPS) or "visitor" in groups:
        raise StaffError("only a staff user who isn't a visitor goes on the allowlist")
    allowlist.allow(username)


def label_users(allowlist: Allowlist, dry_run: bool) -> dict[str, int]:
    cognito, pool = allowlist.cognito, allowlist.pool
    if next(members(cognito, pool, "visitor"), None) is not None:
        raise StaffError(
            "the pool holds a visitor, so its staff can't be trusted wholesale; "
            "put each on the allowlist with make staff"
        )
    labeled = {u["Username"] for label in LABELS for u in members(cognito, pool, label)}
    unlabeled = [
        u["Username"]
        for u in members(cognito, pool, "customer")
        if u["Username"] not in labeled
    ]
    staff = {
        u["Username"]: sub_in(u)
        for group in STAFF_GROUPS
        for u in members(cognito, pool, group)
    }
    moves = [
        (
            username,
            "evaluation" if username == smoke_user.USERNAME else "invited",
        )
        for username in unlabeled
    ]
    if not dry_run:
        for username, label in moves:
            cognito.admin_add_user_to_group(
                UserPoolId=pool, Username=username, GroupName=label
            )
        for username, sub in staff.items():
            allowlist.allow(username, sub)
    return {
        "invited": sum(1 for _, label in moves if label == "invited"),
        "smoke": sum(1 for _, label in moves if label == "evaluation"),
        "staff": len(staff),
    }


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m faro.staff", description=__doc__)
    parser.add_argument("command", choices=("add", "remove", "label-users"))
    parser.add_argument("--stack", type=Path, required=True)
    parser.add_argument("--user")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.command != "label-users" and not args.user:
        parser.error(f"{args.command} needs --user")
    return args


def run(args: argparse.Namespace, cognito: Any, ssm: Any) -> str:
    outputs = judges.stack_outputs(args.stack)
    if "staff_allowlist" not in outputs:
        raise StaffError(
            f"{args.stack} has no staff_allowlist; apply, then make outputs"
        )
    allowlist = Allowlist(
        cognito, ssm, outputs["user_pool_id"], outputs["staff_allowlist"]
    )
    if args.command == "add":
        add(allowlist, args.user)
        return "put the user on the staff allowlist"
    if args.command == "remove":
        taken = allowlist.disallow(args.user)
        return (
            "took the user off the staff allowlist"
            if taken
            else "the user wasn't on it"
        )
    counts = label_users(allowlist, args.dry_run)
    verb = "would label" if args.dry_run else "labeled"
    return (
        f"{verb} {counts['invited']} customers invited and {counts['smoke']} smoke user "
        f"evaluation, and {'would put' if args.dry_run else 'put'} {counts['staff']} staff users "
        "on the allowlist"
    )


def main(argv: Sequence[str] | None = None) -> None:
    args = parse(argv)
    try:
        print(
            run(
                args,
                boto3.client("cognito-idp", region_name="us-east-1"),
                boto3.client("ssm", region_name="us-east-1"),
            )
        )
    except (StaffError, AllowlistError, judges.JudgesError, ClientError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
