"""
The build's record in the export's manifest (ADR-0006, Lineage): the versions that made it, the rows of every model, and
every check's result, with its severity, its rows, and their share of the model's. It holds nothing that varies between
two builds of the same inputs, so rebuilding gives the same manifest, and it follows the publication rule (SEC-03):
aggregates only, and counts from 1 to 9, with their shares, shown as <10. The items' own counts, which Terraform and the
import's check read, are the writer's and aren't suppressed.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import duckdb

from faro.analysis.source import one
from faro.pipeline import version
from faro.pipeline.runner import Result

SUPPRESSED = "<10"


def suppressed(count: int) -> int | str:
    return SUPPRESSED if 0 < count < 10 else count


def _share(count: int, rows: int | None) -> float | None:
    if not rows or suppressed(count) == SUPPRESSED:
        return None
    return round(count / rows, 6)


def model_rows(
    con: duckdb.DuckDBPyConnection, dbt_manifest: Mapping[str, Any]
) -> dict[str, int]:
    rows = {}
    for node in dbt_manifest["nodes"].values():
        if node["resource_type"] == "model":
            relation = f"{node['schema']}.{node['alias']}"
            rows[node["unique_id"]] = int(
                one(con, f"select count(*) from {relation}")[0]
            )
    return rows


def _tested_model(node: Mapping[str, Any], models: Mapping[str, str]) -> str | None:
    if node.get("attached_node"):
        return str(node["attached_node"])
    if node.get("model"):
        return next(
            (uid for uid, name in models.items() if name == node["model"]), None
        )
    named = [ref["name"] for ref in node.get("refs", [])]
    return next(
        (uid for uid, name in models.items() if named and name == named[0]), None
    )


def checks(
    results: Sequence[Result],
    dbt_manifest: Mapping[str, Any],
    rows: Mapping[str, int],
) -> list[dict[str, Any]]:
    nodes = {**dbt_manifest["nodes"], **dbt_manifest.get("unit_tests", {})}
    models = {
        uid: node["name"]
        for uid, node in dbt_manifest["nodes"].items()
        if node["resource_type"] == "model"
    }
    recorded = []
    for result in results:
        node = nodes[result["unique_id"]]
        if node["resource_type"] not in ("test", "unit_test"):
            continue
        tested = _tested_model(node, models)
        failures = int(result.get("failures") or 0)
        severity = str(node.get("config", {}).get("severity", "error")).lower()
        recorded.append(
            {
                "name": node["name"],
                "kind": node["resource_type"],
                "model": models.get(tested or ""),
                "severity": severity,
                "status": result["status"],
                "rows": suppressed(failures),
                "share": _share(failures, rows.get(tested or "")),
            }
        )
    return sorted(recorded, key=lambda check: (check["kind"], check["name"]))


def run(
    con: duckdb.DuckDBPyConnection,
    results: Sequence[Result],
    dbt_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    rows = model_rows(con, dbt_manifest)
    names = {
        uid: f"{node['schema']}.{node['alias']}"
        for uid, node in dbt_manifest["nodes"].items()
        if node["resource_type"] == "model"
    }
    return {
        "versions": version.versions(),
        "rows": {names[uid]: suppressed(n) for uid, n in rows.items()},
        "checks": checks(results, dbt_manifest, rows),
    }
