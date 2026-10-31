"""
An export is written in DynamoDB JSON under items/, checked against the tools' data contract, the same bytes each time,
and uploaded once: the manifest last, and never over another export (ADR-0006; DML-01, DML-02, DML-04).
"""

import copy
import gzip
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from mypy_boto3_s3 import S3Client

from faro.export.items import (
    ITEMS,
    MANIFEST,
    ExportError,
    attribute,
    check,
    line,
    prefix,
    upload,
    write,
)

from ..conftest import tools_data_example

STAMP = {"snapshot": "b3b8b248f604ef9a", "pipeline_version": "5e1a9c3b7d2f4a68"}
CLOCK = {"business_date": "2026-06-17", "as_of": "2026-06-18 06:00:00"}


def items() -> list[dict[str, Any]]:
    return tools_data_example()


def written(
    directory: Path, given: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return write(
        items() if given is None else given,
        directory,
        stamp=STAMP,
        clock=CLOCK,
        producer="test",
    )


def test_values_take_their_dynamodb_types() -> None:
    assert attribute(None) == {"NULL": True}
    assert attribute(True) == {"BOOL": True}
    assert attribute(1) == {"N": "1"}
    assert attribute(Decimal("189.90")) == {"N": "189.90"}
    assert attribute({"a": "b"}) == {"M": {"a": {"S": "b"}}}
    with pytest.raises(ExportError, match="float"):
        attribute(189.9)


def test_each_line_is_one_item() -> None:
    assert json.loads(line({"pk": "META", "schema_version": 1})) == {
        "Item": {"pk": {"S": "META"}, "schema_version": {"N": "1"}}
    }


def test_the_items_and_manifest_are_the_same_bytes_each_time(tmp_path: Path) -> None:
    first = written(tmp_path / "a")
    second = written(tmp_path / "b")

    assert (tmp_path / "a" / ITEMS).read_bytes() == (
        tmp_path / "b" / ITEMS
    ).read_bytes()
    assert first == second
    assert first["items"] == {
        "metadata": 1,
        "customer": 1,
        "card": 1,
        "transaction": 1,
        "total": 4,
    }
    assert (first["snapshot"], first["pipeline_version"], first["clock"]) == (
        STAMP["snapshot"],
        STAMP["pipeline_version"],
        CLOCK,
    )
    lines = gzip.decompress((tmp_path / "a" / ITEMS).read_bytes()).decode().splitlines()
    assert [json.loads(text)["Item"]["sk"]["S"] for text in lines] == [
        "CARD#PRD-EXAMPLE00002",
        "CUSTOMER",
        "TXN#TRX-EXAMPLE0000000000003",
        "META",
    ]


def test_an_item_outside_the_contract_stops_the_export(tmp_path: Path) -> None:
    given = items()
    given[2]["last_four"] = "48210"

    with pytest.raises(ExportError, match="item 2"):
        written(tmp_path, given)
    assert not (tmp_path / MANIFEST).exists()


@pytest.mark.parametrize(
    ("index", "name", "value", "problem"),
    [
        (2, "sk", "CARD#PRD-EXAMPLE00009", "sk doesn't match"),
        (3, "card_key", "CLI-EXAMPLE00001#PRD-EXAMPLE00009", "card_key doesn't match"),
        (
            3,
            "listed_at",
            "2026-06-14 21:07:34#TRX-EXAMPLE0000000000003",
            "listed_at doesn't match",
        ),
        (
            0,
            "stamp",
            {"snapshot": "b3b8b248f604ef9a", "pipeline_version": "0" * 16},
            "stamp",
        ),
    ],
)
def test_keys_and_the_stamp_must_agree_with_the_items(
    index: int, name: str, value: Any, problem: str
) -> None:
    given = items()
    given[index][name] = value

    with pytest.raises(ExportError, match=problem):
        check(given, STAMP)


def test_no_record_takes_the_fixtures_prefix() -> None:
    given = items()
    card = next(i for i in given if i["kind"] == "card")
    transaction = next(i for i in given if i["kind"] == "transaction")
    old, new = card["card_id"], "PRD-FIXTUREA0001"
    for item in (card, transaction):
        for name in ("sk", "card_id", "card_key"):
            if name in item:
                item[name] = item[name].replace(old, new)

    with pytest.raises(ExportError, match="card_id begins with the fixtures' prefix"):
        check(given, STAMP)


def test_items_must_have_one_key_one_metadata_item_and_their_card() -> None:
    doubled = items() + [copy.deepcopy(items()[1])]
    with pytest.raises(ExportError, match="share a key"):
        check(doubled, STAMP)
    with pytest.raises(ExportError, match="0 metadata items"):
        check(items()[1:], STAMP)
    with pytest.raises(ExportError, match="no card of its customer's"):
        check([i for i in items() if i["kind"] != "card"], STAMP)


def test_the_upload_puts_the_manifest_last_and_only_once(
    tmp_path: Path, bucket: tuple[S3Client, str]
) -> None:
    s3, name = bucket
    written(tmp_path)
    root = prefix(STAMP["snapshot"], STAMP["pipeline_version"])

    assert upload(s3, name, tmp_path, log=lambda _: None) == root
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=name)["Contents"]]
    assert sorted(keys) == [root + ITEMS, root + MANIFEST]
    manifest = s3.get_object(Bucket=name, Key=root + MANIFEST)
    assert manifest["ContentType"] == "application/json"

    logged: list[str] = []
    upload(s3, name, tmp_path, log=logged.append)
    assert logged == [f"s3://{name}/{root} is complete; nothing to upload"]


def test_another_export_under_the_same_version_is_refused(
    tmp_path: Path, bucket: tuple[S3Client, str]
) -> None:
    s3, name = bucket
    written(tmp_path / "first")
    upload(s3, name, tmp_path / "first", log=lambda _: None)
    changed = items()
    changed[2]["current_balance"] = Decimal("1.00")
    written(tmp_path / "second", changed)

    with pytest.raises(ExportError, match="another export under the same version"):
        upload(s3, name, tmp_path / "second", log=lambda _: None)


def test_items_left_by_an_upload_that_stopped_are_kept_when_unchanged(
    tmp_path: Path, bucket: tuple[S3Client, str]
) -> None:
    s3, name = bucket
    written(tmp_path)
    root = prefix(STAMP["snapshot"], STAMP["pipeline_version"])
    s3.put_object(Bucket=name, Key=root + ITEMS, Body=(tmp_path / ITEMS).read_bytes())

    upload(s3, name, tmp_path, log=lambda _: None)

    assert (
        json.loads(s3.get_object(Bucket=name, Key=root + MANIFEST)["Body"].read())[
            "items"
        ]["total"]
        == 4
    )
