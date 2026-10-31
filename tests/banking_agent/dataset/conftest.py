"""
A folder stands in for a copy of the organizers' dataset, and moto for this project's data bucket.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from banking_agent.dataset.lock import LockedFile, make_lock, write_lock
from banking_agent.dataset.snapshot import data_bucket_name

MOTO_ACCOUNT_ID = "123456789012"


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """
    Fake credentials in throwaway config files, so no test can reach a real profile.
    """
    config = tmp_path / "aws-config"
    config.write_text("")
    credentials = tmp_path / "aws-credentials"
    credentials.write_text(
        "[default]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv("AWS_REGION", "us-east-1")
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
def source_files() -> dict[str, bytes]:
    return {
        "customers.csv": b"customer_id,first_name\nC1,Ana\n",
        "transactions/year=2023/month=06/day=17/transactions_20230617.csv": b"transaction_id\nT1\n",
        "transactions/year=2023/month=06/day=18/transactions_20230618.csv": b"transaction_id\nT2\n",
    }


@pytest.fixture
def lock_path(tmp_path: Path, source_files: dict[str, bytes]) -> Path:
    path = tmp_path / "dataset.lock"
    files = (
        LockedFile(key, len(body), "etag", hashlib.sha256(body).hexdigest())
        for key, body in source_files.items()
    )
    write_lock(path, make_lock("s3://bucket/data/", files))
    return path


@pytest.fixture
def copy(tmp_path: Path, source_files: dict[str, bytes]) -> Path:
    """
    A copy of the dataset, laid out like the organizers' data/ prefix.
    """
    root = tmp_path / "copy"
    for key, body in source_files.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return root


@pytest.fixture
def destination(aws: None) -> tuple[S3Client, str]:
    s3 = boto3.client("s3", region_name="us-east-1")
    bucket = data_bucket_name(MOTO_ACCOUNT_ID, "us-east-1")
    s3.create_bucket(Bucket=bucket)
    return s3, bucket
