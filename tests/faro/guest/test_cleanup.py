"""
The cleanup deletes the visitors made more than a day before, and no other user, whatever its groups (ADR-0007, The
guest entry, Cleanup; OPS-10).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest

from faro.guest.cleanup import sweep


@pytest.fixture
def pool(aws: None) -> tuple[Any, str]:
    cognito = boto3.client("cognito-idp", region_name="us-east-1")
    pool = cognito.create_user_pool(PoolName="banking-agent-local")["UserPool"]["Id"]
    for group in ("customer", "visitor", "invited", "evaluation"):
        cognito.create_group(UserPoolId=pool, GroupName=group)
    users = {
        "visitor-1": ("visitor", "customer"),
        "visitor-2": ("visitor", "customer"),
        "visitor-unfinished": ("visitor",),
        "judge": ("customer", "invited"),
        "smoke": ("customer", "evaluation"),
        "unlabeled": ("customer",),
    }
    for username, groups in users.items():
        cognito.admin_create_user(
            UserPoolId=pool, Username=username, MessageAction="SUPPRESS"
        )
        for group in groups:
            cognito.admin_add_user_to_group(
                UserPoolId=pool, Username=username, GroupName=group
            )
    return cognito, pool


def remaining(cognito: Any, pool: str) -> set[str]:
    return {u["Username"] for u in cognito.list_users(UserPoolId=pool)["Users"]}


def test_visitors_a_day_old_are_deleted_and_no_one_else(pool: tuple[Any, str]) -> None:
    cognito, pool_id = pool
    now = datetime.now(UTC)

    assert sweep(cognito, pool_id, now + timedelta(hours=1)) == 0
    assert sweep(cognito, pool_id, now + timedelta(days=1, minutes=5)) == 3
    assert remaining(cognito, pool_id) == {"judge", "smoke", "unlabeled"}


def test_a_visitor_already_gone_is_passed_over(
    pool: tuple[Any, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cognito, pool_id = pool
    delete = cognito.admin_delete_user

    def raced(**request: Any) -> Any:
        if request["Username"] == "visitor-1":
            delete(**request)
        return delete(**request)

    monkeypatch.setattr(cognito, "admin_delete_user", raced)

    assert sweep(cognito, pool_id, datetime.now(UTC) + timedelta(days=2)) == 2
    assert remaining(cognito, pool_id) == {"judge", "smoke", "unlabeled"}
