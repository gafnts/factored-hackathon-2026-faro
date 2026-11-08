"""
make cap: it prints the cap and the day's counts, sets the turns' and the guests' cap an administrator chooses, 0 turns
pausing the chat for the Runtime from the next request, and changes only a cap Terraform wrote, adding the guests' to
one written before them (ADR-0004's amendments of 2026-11-07 and 2026-11-08; OPS-08).
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from faro import cap
from faro.agent.usage import CAP_KEY, DynamoUsage
from faro.guest.usage import admit

TABLE = "banking-agent-local-usage-counters"
NOW = datetime(2026, 11, 7, 23, 30, tzinfo=UTC)
SIGN_IN = "5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68"
SUB = "a41c9e27-6b3d-4f58-9e12-7c0d8b5a3f64"


@pytest.fixture
def dynamodb(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Any]:
    credentials = tmp_path / "aws-credentials"
    credentials.write_text(
        "[default]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("counter", "window")
            ],
            KeySchema=[
                {"AttributeName": "counter", "KeyType": "HASH"},
                {"AttributeName": "window", "KeyType": "RANGE"},
            ],
        )
        yield client


@pytest.fixture
def stack(tmp_path: Path) -> Path:
    path = tmp_path / "local.outputs.json"
    path.write_text(
        json.dumps({"runtime_tables": {"value": {"usage_counters": TABLE}}})
    )
    return path


def with_cap(dynamodb: Any, turns: int, guests: int | None = 100) -> None:
    item = {**CAP_KEY, "turns": {"N": str(turns)}}
    if guests is not None:
        item["guests"] = {"N": str(guests)}
    dynamodb.put_item(TableName=TABLE, Item=item)


def test_it_prints_the_cap_and_the_turns_and_guests_counted_today(
    dynamodb: Any, stack: Path
) -> None:
    with_cap(dynamodb, 750)
    usage = DynamoUsage(dynamodb, TABLE)
    for _ in range(3):
        usage.count(SIGN_IN, SUB, "visitor", NOW)
    usage.count(SIGN_IN, SUB, "evaluation", NOW)
    for _ in range(2):
        admit(dynamodb, TABLE, NOW)

    printed = cap.run(cap.parse(["--stack", str(stack)]), dynamodb, NOW)

    assert printed == (
        "The cap is 750 turns a day; the guests' cap is 100 a day; "
        "3 turns and 2 guests counted today (UTC)."
    )


def test_a_cap_of_0_pauses_the_runtime_from_the_next_request_and_a_cap_resumes_it(
    dynamodb: Any, stack: Path
) -> None:
    with_cap(dynamodb, 750)
    usage = DynamoUsage(dynamodb, TABLE)

    paused = cap.run(cap.parse(["--stack", str(stack), "--turns", "0"]), dynamodb, NOW)
    refused = usage.count(SIGN_IN, SUB, "demo", NOW)
    resumed = cap.run(
        cap.parse(["--stack", str(stack), "--turns", "750"]), dynamodb, NOW
    )

    assert paused == (
        "The turns' cap was 750 turns a day and is 0 (paused); "
        "0 turns and 0 guests counted today (UTC)."
    )
    assert refused == "paused"
    assert resumed == (
        "The turns' cap was 0 (paused) and is 750 turns a day; "
        "0 turns and 0 guests counted today (UTC)."
    )
    assert usage.count(SIGN_IN, SUB, "demo", NOW) is None


def test_it_changes_no_cap_terraform_didnt_write(dynamodb: Any, stack: Path) -> None:
    with pytest.raises(cap.CapError):
        cap.write(dynamodb, TABLE, 0)
    with pytest.raises(cap.CapError):
        cap.run(cap.parse(["--stack", str(stack)]), dynamodb, NOW)

    assert "Item" not in dynamodb.get_item(TableName=TABLE, Key=CAP_KEY)


def test_a_cap_written_before_the_guests_gets_them(dynamodb: Any, stack: Path) -> None:
    with_cap(dynamodb, 750, guests=None)

    before = cap.run(cap.parse(["--stack", str(stack)]), dynamodb, NOW)
    added = cap.run(
        cap.parse(["--stack", str(stack), "--guests", "100"]), dynamodb, NOW
    )

    assert before == (
        "The cap is 750 turns a day; the guests' cap is unset, so the guest entry makes "
        "no user; 0 turns and 0 guests counted today (UTC)."
    )
    assert added == (
        "The guests' cap was unset and is 100 a day; 0 turns and 0 guests counted today (UTC)."
    )
    assert dynamodb.get_item(TableName=TABLE, Key=CAP_KEY)["Item"] == {
        **CAP_KEY,
        "turns": {"N": "750"},
        "guests": {"N": "100"},
    }


@pytest.mark.parametrize("flag", ["--turns", "--guests"])
def test_a_cap_below_0_is_refused(flag: str) -> None:
    with pytest.raises(SystemExit):
        cap.parse(["--stack", "local.outputs.json", flag, "-1"])
