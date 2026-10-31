"""
The snapshot as the oracle and the generator read it: the pipeline's bronze tables (ADR-0006), the typed copy of the
delivered files, never the gold tables the tools read (ADR-0005, The oracle); only the build's stamp and clock are
read from gold, besides the tools' items the in-process player serves, which the oracle never reads. A connection is
opened for one side of the split and sees only that side's customers, their cards, and their transactions (DML-09);
this module is the only one that names the pipeline's database.
"""

import json
from collections.abc import Collection
from pathlib import Path
from typing import Any

import duckdb
from duckdb import sqltypes

from faro.pipeline import export
from faro.split import held_out

SIDES = ("development", "held_out")
TABLES = ("customers", "products", "transactions")


def connect(database: Path, side: str) -> duckdb.DuckDBPyConnection:
    """
    An in-memory connection with the database attached read-only and a view per table, named as bronze names it.
    """
    if side not in SIDES:
        raise ValueError(f"no side named {side}")
    con = duckdb.connect()
    con.execute(f"attach '{database.resolve()}' as pipeline (read_only)")
    con.create_function("held_out", held_out, [sqltypes.VARCHAR], sqltypes.BOOLEAN)
    con.execute(
        "create temp table side_customers as "
        "select distinct customer_id from pipeline.bronze.customers where held_out(customer_id) = $held",
        {"held": side == "held_out"},
    )
    for table in ("customers", "products"):
        con.execute(
            f"create temp view {table} as select * from pipeline.bronze.{table} "
            "where customer_id in (select customer_id from side_customers)"
        )
    # By card, as the tools list a card's transactions.
    con.execute(
        "create temp view transactions as select * from pipeline.bronze.transactions "
        "where product_id in (select product_id from products)"
    )
    return con


def tools_items(database: Path, customers: Collection[str]) -> list[dict[str, Any]]:
    """
    The tools' data for these customers only, as the export writes it: gold read through views named as the export
    reads them, so the export's own code builds the items.
    """
    con = duckdb.connect()
    con.execute(f"attach '{database.resolve()}' as pipeline (read_only)")
    con.execute("create table wanted (customer_id varchar)")
    con.executemany("insert into wanted values (?)", [(c,) for c in customers])
    con.execute("create schema gold")
    for table in ("customers", "cards", "transactions"):
        con.execute(
            f"create view gold.{table} as select * from pipeline.gold.{table} "
            "where customer_id in (select customer_id from wanted)"
        )
    con.execute("create view gold.metadata as select * from pipeline.gold.metadata")
    built, _ = stamp(con)
    items = export.read_items(con, built)
    con.close()
    # As the export's JSON holds them, numbers and all.
    loaded: list[dict[str, Any]] = json.loads(json.dumps(items, default=float))
    return loaded


def stamp(con: duckdb.DuckDBPyConnection) -> tuple[dict[str, str], tuple[Any, Any]]:
    """
    The build's own snapshot and pipeline version, which a set records, and its clock, which must be the one the
    policy states, since the oracle reads every window from that.
    """
    row = con.execute(
        "select snapshot_id, pipeline_version, business_date, as_of from pipeline.gold.metadata"
    ).fetchone()
    assert row is not None
    snapshot, version, business_date, as_of = row
    return {"snapshot": snapshot, "pipeline_version": version}, (business_date, as_of)
