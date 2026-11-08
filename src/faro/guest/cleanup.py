"""
The visitors' cleanup (ADR-0007, The guest entry, Cleanup): every hour, it deletes the users in the visitor group made
more than a day before, as the evaluation harness deletes its own after a run. Its role may delete any of the pool's
users, since IAM can't hold it to a group, so its code lists the visitor group alone and deletes only what it listed.
A visitor's records, checkpoints, and cases keep the retention ADR-0004 gives them. It logs a count, never a username
(SEC-03).
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

GROUP = "visitor"
AGE = timedelta(days=1)


def sweep(cognito: Any, pool: str, now: datetime) -> int:
    # Listed in full first, since deleting while paging can skip a page's users.
    pages = cognito.get_paginator("list_users_in_group").paginate(
        UserPoolId=pool, GroupName=GROUP
    )
    due = [
        u["Username"]
        for page in pages
        for u in page["Users"]
        if now - u["UserCreateDate"] > AGE
    ]
    deleted = 0
    for username in due:
        try:
            cognito.admin_delete_user(UserPoolId=pool, Username=username)
        except ClientError as error:
            if error.response["Error"]["Code"] != "UserNotFoundException":
                raise
            continue
        deleted += 1
    return deleted


def handler(event: Any, context: Any) -> dict[str, int]:
    deleted = sweep(
        boto3.client("cognito-idp"), os.environ["USER_POOL_ID"], datetime.now(UTC)
    )
    logger.info("visitors deleted", extra={"deleted": deleted})
    return {"deleted": deleted}
