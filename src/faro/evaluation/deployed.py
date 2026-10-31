"""
A deployed stack as the harness reaches it (ADR-0005, Running a case): read from its Terraform outputs, and refused when
its tools serve another snapshot than the set's, or another clock than the policy's, since the oracle read the set from
that snapshot at that clock. The harness takes the stack's evaluation role when the stack has one, so a run holds the
role's permissions and no others (ADR-0004's amendment of 2026-10-01); a stack applied before the role runs on the
caller's credentials, and the run's manifest says which.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import boto3
import botocore.session
from botocore.credentials import RefreshableCredentials

from faro.evaluation.state import AS_OF, BUSINESS_DATE

CALLER = "caller"


class StackError(ValueError):
    pass


@dataclass(frozen=True)
class Deployed:
    environment: str
    region: str
    user_pool_id: str
    client_id: str
    invoke_url: str
    gateway_url: str
    gateway_targets: Mapping[str, str]
    records_table: str
    overlay_table: str
    confirmations_table: str
    cases_table: str
    stamp: Mapping[str, str]
    clock: Mapping[str, str]
    role_arn: str | None
    bucket: str | None

    @property
    def stop_url(self) -> str:
        return self.invoke_url.replace("/invocations?", "/stopruntimesession?")


def read(path: Path) -> Deployed:
    if not path.is_file():
        raise StackError(f"no stack outputs at {path}")
    outputs = {
        name: value["value"]
        for name, value in json.loads(path.read_text(encoding="utf-8")).items()
    }
    evaluation = outputs.get("evaluation") or {}
    return Deployed(
        environment=outputs["environment"],
        region=outputs["runtime_arn"].split(":")[3],
        user_pool_id=outputs["user_pool_id"],
        client_id=outputs["customer_client_id"],
        invoke_url=outputs["invoke_url"],
        gateway_url=outputs["gateway_url"],
        gateway_targets=outputs["gateway_targets"],
        records_table=outputs["runtime_tables"]["execution_records"],
        overlay_table=outputs["sandbox_tables"]["overlay"],
        confirmations_table=outputs["sandbox_tables"]["confirmations"],
        cases_table=outputs["handoff"]["cases_table"],
        stamp=outputs["tools_data"]["stamp"],
        clock=outputs["tools_data"]["clock"],
        role_arn=evaluation.get("role_arn"),
        bucket=evaluation.get("bucket"),
    )


def check(stack: Deployed, versions: Mapping[str, Any]) -> None:
    """
    versions is the set's manifest's.
    """
    built = {k: versions[k] for k in ("snapshot", "pipeline_version")}
    if dict(stack.stamp) != built:
        raise StackError("the stack's tools serve another snapshot than the set's")
    policy = {
        "business_date": BUSINESS_DATE.isoformat(),
        "as_of": AS_OF.isoformat(sep=" "),
    }
    if dict(stack.clock) != policy:
        raise StackError("the stack's clock isn't the policy's")


def session(
    stack: Deployed, run: str, base: boto3.Session | None = None
) -> tuple[boto3.Session, str]:
    """
    The session a run reaches the stack with, and what it runs as: the role's ARN, or the caller. The role's
    credentials are refreshed before they expire, so a run may outlast a role session's hour.
    """
    base = base or boto3.Session(region_name=stack.region)
    if stack.role_arn is None:
        return base, CALLER
    sts = base.client("sts", region_name=stack.region)
    role_arn = stack.role_arn

    def assumed() -> dict[str, str]:
        given = sts.assume_role(RoleArn=role_arn, RoleSessionName=f"evaluation-{run}")
        credentials = given["Credentials"]
        expiration: datetime = credentials["Expiration"]
        return {
            "access_key": credentials["AccessKeyId"],
            "secret_key": credentials["SecretAccessKey"],
            "token": credentials["SessionToken"],
            "expiry_time": expiration.isoformat(),
        }

    core = botocore.session.get_session()
    # botocore sets refreshable credentials on a session only this way.
    core._credentials = RefreshableCredentials.create_from_metadata(  # type: ignore[attr-defined]
        assumed(), assumed, "assume-role"
    )
    return boto3.Session(botocore_session=core, region_name=stack.region), role_arn
