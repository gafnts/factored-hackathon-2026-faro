"""
make export writes gold's items in parts, stamped with the snapshot and a version that hashes what shapes them, with the
run's manifest beside them and, the same file, under docs/pipeline/; nothing in it varies between builds of the same
inputs, and counts under 10 are suppressed (DML-01, DML-04, SEC-03).
"""

import gzip
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
from mypy_boto3_s3 import S3Client

from faro.export import items as writer
from faro.pipeline import export, manifest, version
from faro.pipeline.__main__ import main

from ..export.conftest import aws, bucket  # noqa: F401
from .conftest import Built

pytestmark = pytest.mark.xdist_group("pipeline")

DOCS = Path(__file__).resolve().parents[3] / "docs" / "pipeline"


def _decoded(value: dict[str, Any]) -> Any:
    [(kind, inner)] = value.items()
    if kind == "N":
        return Decimal(inner) if "." in inner else int(inner)
    if kind == "NULL":
        return None
    if kind == "M":
        return {k: _decoded(v) for k, v in inner.items()}
    return inner


def _read_parts(directory: Path, written: dict[str, Any]) -> list[dict[str, Any]]:
    read = []
    for key in written["objects"]:
        for text in (
            gzip.decompress((directory / key).read_bytes()).decode().splitlines()
        ):
            read.append({k: _decoded(v) for k, v in json.loads(text)["Item"].items()})
    return read


def test_the_version_hashes_what_shapes_an_export_and_nothing_else(
    tmp_path: Path,
) -> None:
    for shape in version.SHAPES:
        path = tmp_path / shape
        if Path(shape).suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n")
        else:
            path.mkdir(parents=True)
            (path / "model.sql").write_text("select 1\n")
    first = version.pipeline_version(tmp_path)

    for ignored in (
        "README.md",
        "target/run.json",
        "logs/dbt.log",
        ".DS_Store",
        "__pycache__/m.pyc",
    ):
        (tmp_path / "pipeline" / ignored).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "pipeline" / ignored).write_text("x")
    assert version.pipeline_version(tmp_path) == first

    (tmp_path / "src/faro/pipeline/model.sql").write_text("select 2\n")
    assert version.pipeline_version(tmp_path) != first
    assert re.fullmatch(r"[0-9a-f]{16}", first)
    assert version.versions() == {
        "dbt-core": "1.12.5",
        "dbt-duckdb": "1.11.0",
        "duckdb": "1.5.5",
    }


def test_the_export_writes_gold_in_parts_beside_its_manifest(
    base: Built, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "PART_ITEMS", 10)

    directory, written = export.export(base.lock, base.data_dir, tmp_path / "docs")

    assert directory == writer.export_dir(
        base.data_dir, base.lock.snapshot_id, base.stamp["pipeline_version"]
    )
    assert list(written["objects"]) == [writer.PART.format(i) for i in range(5)]
    with duckdb.connect(str(base.space.database), read_only=True) as con:
        gold = export.read_items(con, base.stamp)
    assert sorted(
        _read_parts(directory, written), key=lambda i: (i["pk"], i["sk"])
    ) == (sorted(gold, key=lambda i: (i["pk"], i["sk"])))
    assert written["producer"] == "pipeline"
    assert written["items"]["total"] == 42
    docs = (
        tmp_path
        / "docs"
        / f"{base.lock.snapshot_id}-{base.stamp['pipeline_version']}.json"
    )
    assert docs.read_bytes() == (directory / writer.MANIFEST).read_bytes()


def test_two_exports_of_one_build_are_the_same_bytes(
    base: Built, tmp_path: Path
) -> None:
    directory, first = export.export(base.lock, base.data_dir, tmp_path / "a")
    parts = {key: (directory / key).read_bytes() for key in first["objects"]}

    _, second = export.export(base.lock, base.data_dir, tmp_path / "b")

    assert first == second
    assert parts == {key: (directory / key).read_bytes() for key in second["objects"]}


def test_the_manifest_records_the_run_and_publishes_only_aggregates(
    base: Built, tmp_path: Path
) -> None:
    _, written = export.export(base.lock, base.data_dir, tmp_path)
    run = written["run"]
    text = json.dumps(written)

    assert run["versions"] == version.versions()
    assert run["rows"]["gold.transactions"] == 19
    assert run["rows"]["gold.customers"] == manifest.SUPPRESSED
    names = {check["name"]: check for check in run["checks"]}
    assert names["bronze_transactions_rows_match_the_records"]["severity"] == "error"
    assert names["bronze_customers_country_dictionary_values"] == {
        "kind": "test",
        "model": "bronze_customers",
        "name": "bronze_customers_country_dictionary_values",
        "rows": manifest.SUPPRESSED,
        "severity": "warn",
        "share": None,
        "status": "warn",
    }
    assert names["silver_cards_last_transaction_date_disagrees"]["share"] == 1.0
    assert {c["kind"] for c in run["checks"]} == {"test", "unit_test"}
    numbers = [*run["rows"].values(), *(c["rows"] for c in run["checks"])]
    assert not [n for n in numbers if isinstance(n, int) and 0 < n < 10]
    assert str(base.data_dir) not in text
    assert "CLI-TEAM" not in text


def test_an_export_refuses_gold_from_other_code_or_no_passing_build(
    base: Built, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(writer.ExportError, match="no passing build"):
        export.export(base.lock, tmp_path / "elsewhere", tmp_path)

    monkeypatch.setattr(version, "pipeline_version", lambda root=None: "f" * 16)
    with pytest.raises(writer.ExportError, match="other code"):
        export.export(base.lock, base.data_dir, tmp_path)


def test_every_committed_manifest_is_a_passing_builds_and_publishes_only_aggregates() -> (
    None
):
    committed = sorted(DOCS.glob("*.json"))

    assert committed
    for path in committed:
        written = json.loads(path.read_text())
        run = written["run"]
        numbers = [*run["rows"].values(), *(c["rows"] for c in run["checks"])]
        assert path.name == f"{written['snapshot']}-{written['pipeline_version']}.json"
        assert {c["status"] for c in run["checks"]} <= {"pass", "warn"}, path.name
        assert not [n for n in numbers if isinstance(n, int) and 0 < n < 10]
        assert not re.search(r"(?<![\w-])[A-Z]{3}-[A-Z0-9]{8}", path.read_text())


def test_suppression_hides_counts_from_one_to_nine() -> None:
    assert [manifest.suppressed(n) for n in (0, 1, 9, 10)] == [0, "<10", "<10", 10]


def test_make_export_uploads_the_parts_then_the_manifest_and_prints_no_customer(
    base: Built,
    tmp_path: Path,
    bucket: tuple[S3Client, str],  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    s3, name = bucket
    args = ["--lock", str(base.lock_path), "--data-dir", str(base.data_dir)]

    assert main([*args, "export", "--docs", str(tmp_path), "--upload"]) == 0

    root = writer.prefix(base.lock.snapshot_id, base.stamp["pipeline_version"])
    listed = s3.list_objects_v2(Bucket=name, Prefix=root)["Contents"]
    assert sorted(o["Key"] for o in listed) == [
        root + writer.ITEMS,
        root + writer.MANIFEST,
    ]
    out = capsys.readouterr().out
    assert f'pipeline_version = "{base.stamp["pipeline_version"]}"' in out
    assert "CLI-TEAM" not in out
