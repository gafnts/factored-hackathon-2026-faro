"""
The command line behind make data and make snapshot (OPS-07).
"""

from pathlib import Path

import pytest
from mypy_boto3_s3 import S3Client

from faro.dataset.__main__ import main
from faro.dataset.lock import read_lock


def options(lock_path: Path, tmp_path: Path) -> list[str]:
    return ["--lock", str(lock_path), "--data-dir", str(tmp_path / "data")]


def test_restores_checks_then_uploads(
    copy: Path,
    lock_path: Path,
    destination: tuple[S3Client, str],
    tmp_path: Path,
) -> None:
    assert main([*options(lock_path, tmp_path), "restore", str(copy)]) == 0
    assert main([*options(lock_path, tmp_path), "check"]) == 0
    assert main([*options(lock_path, tmp_path), "upload"]) == 0

    s3, bucket = destination
    marker = f"snapshots/{read_lock(lock_path).snapshot_id}/dataset.lock"
    assert s3.head_object(Bucket=bucket, Key=marker)["ContentLength"] > 0


def test_check_explains_how_to_restore(
    lock_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*options(lock_path, tmp_path), "check"]) == 1
    assert "make data FROM=<folder>" in capsys.readouterr().err


def test_explains_a_missing_lock(
    destination: tuple[S3Client, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([*options(tmp_path / "dataset.lock", tmp_path), "upload"]) == 1
    assert "dataset.lock not found" in capsys.readouterr().err


def test_explains_an_aws_error_without_a_traceback(
    aws: None,
    lock_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AWS_PROFILE", "missing")

    assert main([*options(lock_path, tmp_path), "upload"]) == 1
    assert (
        "AWS error: The config profile (missing) could not be found"
        in capsys.readouterr().err
    )
