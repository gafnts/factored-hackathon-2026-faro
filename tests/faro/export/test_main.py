"""
make personas, make tiny-export, and make guest-list print counts and the export's version, never a customer's ID or
values (SEC-03).
"""

import json
import re
from pathlib import Path

import boto3
import pytest
from mypy_boto3_s3 import S3Client

from faro.dataset.lock import Lock, write_lock
from faro.export.__main__ import main
from faro.export.items import MANIFEST, prefix
from faro.export.tiny import pipeline_version
from faro.guest.customers import decode

from ..conftest import GUEST_FILES, PERSONA_FILES


def arguments(tmp_path: Path, lock: Lock) -> list[str]:
    write_lock(tmp_path / "dataset.lock", lock)
    profile = tmp_path / "profiling.json"
    profile.write_text(
        json.dumps(
            {
                "snapshot_id": lock.snapshot_id,
                "business_date": "2026-06-17",
                "as_of": "2026-06-18 06:00:00",
            }
        )
    )
    return [
        "--lock",
        str(tmp_path / "dataset.lock"),
        "--data-dir",
        str(tmp_path / "data"),
        "--profile",
        str(profile),
    ]


@pytest.fixture
def paths(tmp_path: Path, persona_bank: tuple[Lock, Path]) -> list[str]:
    return arguments(tmp_path, persona_bank[0])


@pytest.fixture
def guest_paths(tmp_path: Path, guest_bank: tuple[Lock, Path]) -> list[str]:
    stack = tmp_path / "local.outputs.json"
    stack.write_text(json.dumps({"prefix": {"value": "banking-agent-local"}}))
    return [*arguments(tmp_path, guest_bank[0]), "guests", "--stack", str(stack)]


def printed_ids(text: str) -> list[str]:
    ids = set(re.findall(r"(?:CLI|PRD|TRX)-[A-Z0-9]+", "".join(PERSONA_FILES.values())))
    return [i for i in ids if i in text]


def test_personas_then_the_tiny_export_upload_and_print_no_customer(
    paths: list[str],
    bucket: tuple[S3Client, str],
    capsys: pytest.CaptureFixture[str],
    persona_bank: tuple[Lock, Path],
) -> None:
    s3, name = bucket
    lock, _ = persona_bank

    assert main([*paths, "personas"]) == 0
    assert main([*paths, "tiny", "--upload"]) == 0

    out = capsys.readouterr()
    assert printed_ids(out.out + out.err) == []
    # Counts per scenario, never a username or a name (SEC-03).
    assert "declines: chosen among 1 development customers" in out.out
    assert "dispute: chosen among 2 development customers" in out.out
    assert "ana.maria.team" not in out.out
    assert "Ana María" not in out.out
    assert f'pipeline_version = "{pipeline_version()}"' in out.out
    root = prefix(lock.snapshot_id, pipeline_version())
    manifest = json.loads(
        s3.get_object(Bucket=name, Key=root + MANIFEST)["Body"].read()
    )
    assert manifest["items"] == {
        "metadata": 1,
        "customer": 2,
        "card": 4,
        "transaction": 6,
        "total": 13,
    }


def test_the_tiny_export_needs_the_personas_first(
    paths: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*paths, "tiny"]) == 1
    assert "run make personas" in capsys.readouterr().err


def test_the_guest_list_is_written_and_printed_as_counts(
    guest_paths: list[str], aws: None, capsys: pytest.CaptureFixture[str]
) -> None:
    ssm = boto3.client("ssm", region_name="us-east-1")

    assert main([*guest_paths, "--dry-run"]) == 0
    assert ssm.describe_parameters()["Parameters"] == []
    assert main(guest_paths) == 0

    out = capsys.readouterr()
    text = ssm.get_parameter(Name="/banking-agent-local/guest-entry/customers")
    assert len(decode(text["Parameter"]["Value"])) == 24
    assert "México, one card: 4 chosen among 5 development customers" in out.out
    assert "Wrote 24 customers to /banking-agent-local/guest-entry/customers" in out.out
    ids = set(re.findall(r"CLI-[A-Z0-9]+", "".join(GUEST_FILES.values())))
    assert [i for i in ids if i in out.out + out.err] == []
    assert "Guest " not in out.out + out.err


def test_a_snapshot_short_of_guests_writes_nothing(
    paths: list[str], tmp_path: Path, aws: None, capsys: pytest.CaptureFixture[str]
) -> None:
    stack = tmp_path / "local.outputs.json"
    stack.write_text(json.dumps({"prefix": {"value": "banking-agent-local"}}))

    assert main([*paths, "guests", "--stack", str(stack)]) == 1
    assert "fewer than 4" in capsys.readouterr().err
    ssm = boto3.client("ssm", region_name="us-east-1")
    assert ssm.describe_parameters()["Parameters"] == []
