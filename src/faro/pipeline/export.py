"""
Gold's rows as the tools' data items (ADR-0006, Where it runs): each gold column is the attribute of the same name, and
each item is keyed into its customer's partition as the tools' data contract lays out. Written apart from the tiny
export's code, which applies the same rules outside dbt, so a test can hold the two to each other.
"""

import json
import shutil
from collections.abc import Iterator, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from faro.dataset.lock import Lock
from faro.export import items as writer
from faro.export.items import ExportError
from faro.pipeline import manifest, runner, version

TIMESTAMP = "%Y-%m-%d %H:%M:%S"
SCHEMA_VERSION = 1
PRODUCER = "pipeline"
# About ten parts for the snapshot's 420,000 items, which DynamoDB imports side by side.
PART_ITEMS = 50_000


def _attribute(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime(TIMESTAMP)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _rows(con: duckdb.DuckDBPyConnection, table: str) -> Iterator[dict[str, Any]]:
    cursor = con.execute(f"select * from gold.{table}")
    names = [column[0] for column in cursor.description]
    for row in cursor.fetchall():
        yield dict(zip(names, (_attribute(v) for v in row), strict=True))


def clock(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    [row] = list(_rows(con, "metadata"))
    return {"business_date": row["business_date"], "as_of": row["as_of"]}


def read_items(
    con: duckdb.DuckDBPyConnection, stamp: Mapping[str, str]
) -> list[dict[str, Any]]:
    [metadata] = list(_rows(con, "metadata"))
    built_from = {
        "snapshot": metadata["snapshot_id"],
        "pipeline_version": metadata["pipeline_version"],
    }
    if built_from != dict(stamp):
        raise ExportError(
            "gold was built from another snapshot or other code than the export's; run make pipeline"
        )
    built: list[dict[str, Any]] = [
        {
            "pk": "META",
            "sk": "META",
            "kind": "metadata",
            "schema_version": SCHEMA_VERSION,
            "stamp": dict(stamp),
            "clock": clock(con),
        }
    ]
    for row in _rows(con, "customers"):
        built.append(
            {"pk": row["customer_id"], "sk": "CUSTOMER", "kind": "customer", **row}
        )
    for row in _rows(con, "cards"):
        customer = row.pop("customer_id")
        built.append(
            {"pk": customer, "sk": f"CARD#{row['card_id']}", "kind": "card", **row}
        )
    for row in _rows(con, "transactions"):
        customer = row.pop("customer_id")
        built.append(
            {
                "pk": customer,
                "sk": f"TXN#{row['transaction_id']}",
                "kind": "transaction",
                "card_key": f"{customer}#{row['card_id']}",
                "listed_at": f"{row['transaction_date']}#{row['transaction_id']}",
                **row,
            }
        )
    return built


def export(lock: Lock, data_dir: Path, docs: Path) -> tuple[Path, dict[str, Any]]:
    """
    Writes the last build's gold as an export under data/exports/, and its manifest, the same file, under docs/.
    """
    space = runner.workspace(data_dir, lock.snapshot_id)
    results = runner.last_results(space)
    if not results or runner.failed(results) or not space.database.is_file():
        raise ExportError(f"no passing build of {lock.snapshot_id}; run make pipeline")
    dbt_manifest = json.loads((space.target / "manifest.json").read_text())
    stamp = {
        "snapshot": lock.snapshot_id,
        "pipeline_version": version.pipeline_version(),
    }
    with duckdb.connect(str(space.database), read_only=True) as con:
        built = read_items(con, stamp)
        record = manifest.run(con, results, dbt_manifest)
        bank_clock = clock(con)
    directory = writer.export_dir(
        data_dir, stamp["snapshot"], stamp["pipeline_version"]
    )
    shutil.rmtree(directory, ignore_errors=True)
    written = writer.write(
        built,
        directory,
        stamp=stamp,
        clock=bank_clock,
        producer=PRODUCER,
        part_items=PART_ITEMS,
        run=record,
    )
    docs.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        directory / writer.MANIFEST,
        docs / f"{stamp['snapshot']}-{stamp['pipeline_version']}.json",
    )
    return directory, written
