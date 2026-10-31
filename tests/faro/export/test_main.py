"""
make personas and make tiny-export print counts and the export's version, never a customer's ID or values (SEC-03).
"""

import json
import re
from pathlib import Path

import pytest
from mypy_boto3_s3 import S3Client

from faro.dataset.lock import Lock, write_lock
from faro.export.__main__ import main
from faro.export.items import MANIFEST, prefix
from faro.export.tiny import pipeline_version

from ..conftest import PERSONA_FILES


@pytest.fixture
def paths(tmp_path: Path, persona_bank: tuple[Lock, Path]) -> list[str]:
    lock, _ = persona_bank
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
