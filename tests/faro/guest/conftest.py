"""
Moto stands in for the stack's services; its credentials live in throwaway files, so no test reaches a profile.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

COUNTERS = "banking-agent-local-usage-counters"


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    credentials = tmp_path / "aws-credentials"
    credentials.write_text(
        "[default]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    for name in (
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    with mock_aws():
        yield


@pytest.fixture
def counters(aws: None) -> Any:
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(
        TableName=COUNTERS,
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
    return client
