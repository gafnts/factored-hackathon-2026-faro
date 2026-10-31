"""
A deployed stack read from its outputs: refused when its tools serve another snapshot or clock than the set's, and
reached with the evaluation role when the stack has one.
"""

import json
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from faro.evaluation import deployed
from faro.evaluation.deployed import StackError

RUNTIME = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/agent-abc"
INVOKE = (
    "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
    "arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2Fagent-abc"
    "/invocations?qualifier=DEFAULT"
)
STAMP = {"snapshot": "0123456789abcdef", "pipeline_version": "fedcba9876543210"}
CLOCK = {"business_date": "2026-06-17", "as_of": "2026-06-18 06:00:00"}


def outputs(**changed: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "environment": "local",
        "user_pool_id": "us-east-1_pool",
        "customer_client_id": "client",
        "runtime_arn": RUNTIME,
        "invoke_url": INVOKE,
        "gateway_url": "https://gateway.example.com/mcp",
        "gateway_targets": {"list_cards": "reads", "block_card": "block"},
        "runtime_tables": {
            "checkpoints": "checkpoints",
            "session_bindings": "bindings",
            "execution_records": "records",
        },
        "sandbox_tables": {"overlay": "overlay", "confirmations": "confirmations"},
        "handoff": {"cases_table": "cases", "function": "file-handoff"},
        "tools_data": {"table": "data", "stamp": STAMP, "clock": CLOCK, "items": 1},
        "evaluation": {
            "role_arn": "arn:aws:iam::123456789012:role/harness",
            "bucket": "evaluation-bucket",
        },
        **changed,
    }
    return {name: {"value": value} for name, value in values.items()}


def written(tmp_path: Path, **changed: Any) -> Path:
    path = tmp_path / "outputs.json"
    path.write_text(json.dumps(outputs(**changed)), encoding="utf-8")
    return path


def test_a_stack_is_read_from_its_outputs(tmp_path: Path) -> None:
    stack = deployed.read(written(tmp_path))

    assert stack.region == "us-east-1"
    assert stack.records_table == "records"
    assert stack.gateway_url == "https://gateway.example.com/mcp"
    assert stack.gateway_targets["list_cards"] == "reads"
    assert (stack.role_arn, stack.bucket) == (
        "arn:aws:iam::123456789012:role/harness",
        "evaluation-bucket",
    )
    assert stack.stop_url == INVOKE.replace("/invocations?", "/stopruntimesession?")


def test_a_stack_applied_before_the_role_has_neither_role_nor_bucket(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outputs.json"
    raw = outputs()
    del raw["evaluation"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    stack = deployed.read(path)

    assert (stack.role_arn, stack.bucket) == (None, None)


def test_a_stack_serving_the_sets_snapshot_at_the_policys_clock_passes(
    tmp_path: Path,
) -> None:
    deployed.check(deployed.read(written(tmp_path)), {**STAMP, "policy": 2})


@pytest.mark.parametrize(
    ("changed", "refusal"),
    [
        (
            {"stamp": {**STAMP, "snapshot": "1111111111111111"}},
            "another snapshot",
        ),
        (
            {"stamp": {**STAMP, "pipeline_version": "1111111111111111"}},
            "another snapshot",
        ),
        ({"clock": {**CLOCK, "business_date": "2026-06-18"}}, "clock"),
    ],
)
def test_a_stack_serving_another_snapshot_or_clock_is_refused(
    tmp_path: Path, changed: dict[str, Any], refusal: str
) -> None:
    data = {"table": "data", "stamp": STAMP, "clock": CLOCK, "items": 1, **changed}
    stack = deployed.read(written(tmp_path, tools_data=data))

    with pytest.raises(StackError, match=refusal):
        deployed.check(stack, STAMP)


def test_missing_outputs_are_refused(tmp_path: Path) -> None:
    with pytest.raises(StackError, match="no stack outputs"):
        deployed.read(tmp_path / "missing.json")


@mock_aws
def test_a_run_takes_the_stacks_role(tmp_path: Path) -> None:
    iam = boto3.client("iam", region_name="us-east-1")
    role = iam.create_role(RoleName="harness", AssumeRolePolicyDocument="{}")["Role"]
    stack = deployed.read(
        written(tmp_path, evaluation={"role_arn": role["Arn"], "bucket": "b"})
    )

    session, runs_as = deployed.session(stack, "20261001T190000Z-ab12")

    assert runs_as == role["Arn"]
    caller = session.client("sts").get_caller_identity()["Arn"]
    assert ":assumed-role/harness/evaluation-20261001T190000Z-ab12" in caller


@mock_aws
def test_a_stack_without_the_role_runs_as_the_caller(tmp_path: Path) -> None:
    path = tmp_path / "outputs.json"
    raw = outputs()
    del raw["evaluation"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    base = boto3.Session(region_name="us-east-1")

    session, runs_as = deployed.session(deployed.read(path), "run", base)

    assert (session, runs_as) == (base, deployed.CALLER)
