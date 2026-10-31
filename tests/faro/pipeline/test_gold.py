"""
Gold is the data half of the tools' contract (ADR-0006, Contracts): its columns, types, nullability, and values are the
contract's, none holds an identity, a contact, or a card number but the last four digits (POL-11, SEC-03), every model
reaches a declared source (DML-04), and its items equal what the tiny export makes of the same customers, two
implementations of the same rules (POL-20, POL-25, POL-26, POL-30, POL-40).
"""

import json
import re
from collections import deque
from datetime import date, datetime
from typing import Any

import duckdb
import pytest
import yaml

from faro.analysis.catalog import CUSTOMERS, PRODUCTS, TRANSACTIONS
from faro.analysis.source import connect, table_keys
from faro.contracts import schema
from faro.export import items as writer
from faro.export import tiny
from faro.pipeline import export, runner
from faro.pipeline.runner import PROJECT

from .conftest import Built

pytestmark = pytest.mark.xdist_group("pipeline")

GOLD = PROJECT / "models" / "gold" / "_gold.yml"
KEYS = {"pk", "sk", "kind", "card_key", "listed_at"}
KINDS = {
    "gold_customers": "customer_item",
    "gold_cards": "card_item",
    "gold_transactions": "transaction_item",
}
TYPES = {"bank_date": "date", "bank_timestamp": "timestamp", "money": "decimal(15,2)"}
PERSONAL = {"identity", "contact", "card_number"}


def _models() -> dict[str, dict[str, Any]]:
    document = yaml.safe_load(GOLD.read_text(encoding="utf-8"))
    return {m["name"]: m for m in document["models"]}


def _resolve(
    defs: dict[str, Any], prop: dict[str, Any]
) -> tuple[dict[str, Any], str, bool]:
    nullable = False
    if "anyOf" in prop:
        options = [o for o in prop["anyOf"] if o.get("type") != "null"]
        nullable = len(options) < len(prop["anyOf"])
        [prop] = options
    name = ""
    while "$ref" in prop:
        name = prop["$ref"].rsplit("/", 1)[-1]
        prop = defs[name]
    return prop, name, nullable


def _gold_type(prop: dict[str, Any], name: str) -> str:
    if name in TYPES:
        return TYPES[name]
    return "boolean" if prop.get("type") == "boolean" else "varchar"


def test_gold_is_the_data_half_of_the_tools_contract() -> None:
    defs = schema("tools-data")["$defs"]
    models = _models()

    for model, kind in KINDS.items():
        properties = {
            name: prop
            for name, prop in defs[kind]["properties"].items()
            if name not in KEYS
        }
        columns = {c["name"]: c for c in models[model]["columns"]}
        assert set(columns) == set(properties) | {"customer_id"}, model
        for name, prop in properties.items():
            resolved, ref, nullable = _resolve(defs, prop)
            column = columns[name]
            constraints = column.get("constraints", [])
            assert column["data_type"] == _gold_type(resolved, ref), (model, name)
            assert nullable == all(c["type"] != "not_null" for c in constraints), (
                model,
                name,
            )
            if "enum" in resolved:
                [check] = [c["expression"] for c in constraints if c["type"] == "check"]
                assert set(re.findall(r"'([^']*)'", check)) == set(resolved["enum"]), (
                    model,
                    name,
                )
    metadata = {c["name"] for c in models["gold_metadata"]["columns"]}
    assert metadata == {"snapshot_id", "pipeline_version"} | set(
        defs["clock"]["properties"]
    )


def test_no_gold_column_holds_an_identity_a_contact_or_a_card_number(
    base: Built,
) -> None:
    manifest = json.loads((base.space.target / "manifest.json").read_text())
    gold = {
        node["name"]: node["columns"]
        for node in manifest["nodes"].values()
        if node["resource_type"] == "model" and node["name"].startswith("gold_")
    }

    assert set(gold) == {*KINDS, "gold_metadata"}
    for model, columns in gold.items():
        for name, column in columns.items():
            tag = column["config"]["meta"]["personal_data"]
            if name == "last_four":
                continue
            assert tag not in PERSONAL, (model, name)


def test_every_gold_model_reaches_a_declared_source(base: Built) -> None:
    manifest = json.loads((base.space.target / "manifest.json").read_text())
    parents = manifest["parent_map"]

    for node in (n for n in manifest["nodes"] if n.startswith("model.pipeline.gold_")):
        seen, queue = set(), deque([node])
        while queue:
            current = queue.popleft()
            seen.add(current)
            queue.extend(p for p in parents.get(current, []) if p not in seen)
        assert any(n.startswith("source.pipeline.snapshot.") for n in seen), node


def test_the_window_check_passed(base: Built) -> None:
    [window] = [
        r
        for r in base.results
        if runner.node_name(r) == "gold_transactions_within_the_window"
    ]

    assert window["status"] == "pass"


def test_gold_items_fit_the_tools_data_contract(base: Built) -> None:
    stamp = base.stamp
    with duckdb.connect(str(base.space.database), read_only=True) as con:
        built = export.read_items(con, stamp)

    writer.check(built, stamp)
    kinds = [item["kind"] for item in built]
    assert (
        kinds.count("customer"),
        kinds.count("card"),
        kinds.count("transaction"),
    ) == (
        9,
        13,
        19,
    )


def test_gold_items_are_the_tiny_exports_for_the_same_customers(base: Built) -> None:
    stamp = base.stamp
    with duckdb.connect(str(base.space.database), read_only=True) as con:
        gold = export.read_items(con, stamp)
        clock = export.clock(con)
    customers = sorted(item["pk"] for item in gold if item["kind"] == "customer")
    tables = (CUSTOMERS, PRODUCTS, TRANSACTIONS)
    raw = connect(base.root, tables, {t.name: table_keys(base.lock, t) for t in tables})
    try:
        tiny_items = tiny.build(
            raw,
            customers,
            stamp=stamp,
            business_date=date.fromisoformat(clock["business_date"]),
            as_of=datetime.strptime(clock["as_of"], tiny.TIMESTAMP),
        )
    finally:
        raw.close()

    def keyed(items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        return {(i["pk"], i["sk"]): i for i in items}

    assert keyed(gold) == keyed(tiny_items)
