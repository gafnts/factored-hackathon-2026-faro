"""
The outside source's second copy: every file under the source's hash with its sums written last, nothing written again
once complete, a copy that stopped part way finished, and a changed source kept as a new copy (ADR-0011, The seal).
"""

import hashlib
import shutil
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from faro.evaluation import families
from faro.evaluation.outside import bucket

from . import outside

BUCKET = "banking-agent-data-123456789012-us-east-1-an"


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def root(tmp_path: Path) -> Path:
    shutil.copytree(outside.ROOT, tmp_path / "sources")
    return tmp_path / "sources"


def keys(s3: S3Client) -> list[str]:
    return sorted(o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"])


def test_every_file_is_kept_under_the_sources_hash(s3: S3Client, root: Path) -> None:
    prefix = bucket.keep(s3, BUCKET, root)

    files = sorted((root / families.OUTSIDE).glob("*.json"))
    assert prefix == f"sources/outside/{families.source_hash(families.OUTSIDE, root)}/"
    assert keys(s3) == sorted(
        [prefix + p.name for p in files] + [prefix + bucket.MARKER]
    )
    sums = s3.get_object(Bucket=BUCKET, Key=prefix + bucket.MARKER)["Body"].read()
    assert sums.decode() == "".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files
    )


def test_a_complete_copy_is_left_alone_and_a_stopped_one_finished(
    s3: S3Client, root: Path
) -> None:
    prefix = f"sources/outside/{families.source_hash(families.OUTSIDE, root)}/"
    first = sorted((root / families.OUTSIDE).glob("*.json"))[0]
    s3.put_object(Bucket=BUCKET, Key=prefix + first.name, Body=first.read_bytes())

    bucket.keep(s3, BUCKET, root)
    kept = keys(s3)
    bucket.keep(s3, BUCKET, root)

    assert prefix + bucket.MARKER in kept
    assert keys(s3) == kept


def test_a_changed_source_is_a_new_copy(s3: S3Client, root: Path) -> None:
    before = bucket.keep(s3, BUCKET, root)
    answers = root / families.OUTSIDE / "answers.json"
    answers.write_text(answers.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    after = bucket.keep(s3, BUCKET, root)

    assert after != before
    assert {k.rsplit("/", 1)[0] for k in keys(s3)} == {
        before.rstrip("/"),
        after.rstrip("/"),
    }


def test_no_source_is_refused(s3: S3Client, tmp_path: Path) -> None:
    with pytest.raises(bucket.BucketError):
        bucket.keep(s3, BUCKET, tmp_path)
