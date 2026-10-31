"""
Writes an export as ADR-0006 lays it out: the items in DynamoDB JSON, gzipped, under items/ in one or more parts
(DynamoDB imports every object under the prefix it is given and fails on anything that isn't an item), and manifest.json
beside them, which the upload writes last as the completion marker. Every item is checked against the tools' data contract, and its keys
against its attributes, before anything is written (DML-02). Nothing here prints an item's values, which are row-level
data (SEC-03).
"""

import base64
import gzip
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from faro.contracts import schema, validator
from faro.tools.fixtures import CARD_PREFIX, TRANSACTION_PREFIX

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

KINDS = ("metadata", "customer", "card", "transaction")
# Reserved for the evaluation's fixtures, so an ID tells a fixture from a record (ADR-0004's amendment of 2026-10-01).
FIXTURE_PREFIXES = (("card_id", CARD_PREFIX), ("transaction_id", TRANSACTION_PREFIX))
PART = "items/part-{:05d}.json.gz"
ITEMS = PART.format(0)
MANIFEST = "manifest.json"
CONTRACT = "tools-data"

Item = Mapping[str, Any]
Log = Callable[[str], None]


class ExportError(Exception):
    pass


def prefix(snapshot: str, pipeline_version: str) -> str:
    return f"gold/{snapshot}/{pipeline_version}/"


def export_dir(data_dir: Path, snapshot: str, pipeline_version: str) -> Path:
    return data_dir / "exports" / snapshot / pipeline_version


def attribute(value: Any) -> dict[str, Any]:
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, str):
        return {"S": value}
    # Floats would carry binary rounding into amounts; the builders hand over Decimals.
    if isinstance(value, int | Decimal):
        return {"N": str(value)}
    if isinstance(value, Mapping):
        return {"M": {k: attribute(v) for k, v in value.items()}}
    raise ExportError(f"no DynamoDB type for a {type(value).__name__}")


def line(item: Item) -> str:
    encoded = {"Item": {name: attribute(value) for name, value in item.items()}}
    return json.dumps(encoded, sort_keys=True, separators=(",", ":"))


def _mismatched_keys(item: Item) -> list[str]:
    kind = item["kind"]
    expected: dict[str, str] = {}
    if kind == "customer":
        expected = {"pk": item["customer_id"]}
    elif kind == "card":
        expected = {"sk": f"CARD#{item['card_id']}"}
    elif kind == "transaction":
        expected = {
            "sk": f"TXN#{item['transaction_id']}",
            "card_key": f"{item['pk']}#{item['card_id']}",
            "listed_at": f"{item['transaction_date']}#{item['transaction_id']}",
        }
    return [name for name, value in expected.items() if item[name] != value]


def check(items: Sequence[Item], stamp: Mapping[str, str]) -> None:
    contract = validator(CONTRACT)
    problems: list[str] = []
    for index, item in enumerate(items):
        errors = sorted(
            {
                ("/" + "/".join(str(p) for p in e.absolute_path), str(e.validator))
                for e in contract.iter_errors(item)
            }
        )
        problems += [f"item {index}: {path} breaks {rule}" for path, rule in errors]
        if not errors:
            problems += [
                f"item {index}: {name} doesn't match its attributes"
                for name in _mismatched_keys(item)
            ]
            problems += [
                f"item {index}: {name} begins with the fixtures' prefix"
                for name, prefix in FIXTURE_PREFIXES
                if str(item.get(name, "")).startswith(prefix)
            ]
    keys = Counter((item.get("pk"), item.get("sk")) for item in items)
    problems += [f"{n} items share a key" for n in keys.values() if n > 1]
    metadata = [item for item in items if item.get("kind") == "metadata"]
    if len(metadata) != 1:
        problems.append(f"{len(metadata)} metadata items, not one")
    elif metadata[0].get("stamp") != dict(stamp):
        problems.append("the metadata item's stamp isn't the export's")
    customers = {item["pk"] for item in items if item.get("kind") == "customer"}
    cards = {
        (item["pk"], item["card_id"]) for item in items if item.get("kind") == "card"
    }
    if any(
        item.get("kind") in ("card", "transaction") and item["pk"] not in customers
        for item in items
    ):
        problems.append("a card or transaction belongs to no customer in the export")
    if any(
        item.get("kind") == "transaction" and (item["pk"], item["card_id"]) not in cards
        for item in items
    ):
        problems.append("a transaction is on no card of its customer's in the export")
    if problems:
        raise ExportError(
            "the export doesn't fit its contract:\n  " + "\n  ".join(problems)
        )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(
    items: Sequence[Item],
    directory: Path,
    *,
    stamp: Mapping[str, str],
    clock: Mapping[str, str],
    producer: str,
    part_items: int | None = None,
    run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Parts hold part_items items each, in key order, so DynamoDB can import them side by side; run is the build's own
    record, which the pipeline adds.
    """
    check(items, stamp)
    ordered = sorted(items, key=lambda item: (item["pk"], item["sk"]))
    lines = [line(item) for item in ordered]
    size = part_items or len(lines)
    objects: dict[str, dict[str, Any]] = {}
    for index, start in enumerate(range(0, len(lines), size)):
        body = "".join(f"{text}\n" for text in lines[start : start + size]).encode()
        # mtime 0 and no file name, so the same items give the same bytes.
        compressed = gzip.compress(body, mtime=0)
        key = PART.format(index)
        (directory / key).parent.mkdir(parents=True, exist_ok=True)
        (directory / key).write_bytes(compressed)
        objects[key] = {"bytes": len(compressed), "sha256": _sha256(compressed)}
    counts = Counter(item["kind"] for item in ordered)
    manifest = {
        "snapshot": stamp["snapshot"],
        "pipeline_version": stamp["pipeline_version"],
        "producer": producer,
        "contract": schema(CONTRACT)["$id"],
        "clock": dict(clock),
        "items": {**{kind: counts[kind] for kind in KINDS}, "total": len(ordered)},
        # Over each kind's items in key order, so the hash doesn't depend on how the objects split them.
        "content": {
            kind: _sha256(
                "\n".join(
                    t for t, i in zip(lines, ordered, strict=True) if i["kind"] == kind
                ).encode()
            )
            for kind in KINDS
        },
        "objects": objects,
    }
    if run is not None:
        manifest["run"] = dict(run)
    (directory / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return manifest


def read_manifest(directory: Path) -> dict[str, Any]:
    path = directory / MANIFEST
    if not path.is_file():
        raise ExportError(f"{path} not found; build the export first")
    manifest: dict[str, Any] = json.loads(path.read_text())
    return manifest


def upload(s3: "S3Client", bucket: str, directory: Path, *, log: Log = print) -> str:
    """
    Puts the items, then the manifest, each only where nothing is yet; an export already there with the same content is
    left as it is, and one with other content under the same version is an error.
    """
    manifest = read_manifest(directory)
    root = prefix(manifest["snapshot"], manifest["pipeline_version"])
    existing = _get(s3, bucket, root + MANIFEST)
    if existing is not None:
        if json.loads(existing) != manifest:
            raise ExportError(
                f"s3://{bucket}/{root} holds another export under the same version; "
                "a change to the code that shapes an export gets a new version"
            )
        log(f"s3://{bucket}/{root} is complete; nothing to upload")
        return root
    for key, described in manifest["objects"].items():
        data = (directory / key).read_bytes()
        if _sha256(data) != described["sha256"]:
            raise ExportError(
                f"{directory / key} changed since its manifest was written"
            )
        _put(s3, bucket, root + key, data, "application/gzip")
    text = (directory / MANIFEST).read_bytes()
    _put(s3, bucket, root + MANIFEST, text, "application/json")
    log(f"s3://{bucket}/{root} is complete")
    return root


def _put(s3: "S3Client", bucket: str, key: str, data: bytes, content_type: str) -> None:
    digest = base64.b64encode(hashlib.sha256(data).digest()).decode()
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            IfNoneMatch="*",
            ChecksumSHA256=digest,
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "PreconditionFailed":
            raise
        # Left by an upload that stopped before its manifest: fine if it holds the same bytes.
        if _get(s3, bucket, key) != data:
            raise ExportError(f"s3://{bucket}/{key} holds other bytes") from error


def _get(s3: "S3Client", bucket: str, key: str) -> bytes | None:
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return None
        raise
