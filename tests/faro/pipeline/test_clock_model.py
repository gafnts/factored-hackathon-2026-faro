"""
The pipeline computes the business clock from bronze by the rule the profile uses, so both read a snapshot as of the
same instant (ADR-0003, ADR-0006 Freshness); on the pinned snapshot, each committed manifest shows it did.
"""

import json
from datetime import date, datetime
from pathlib import Path

import duckdb
import pytest

from faro.analysis.catalog import TABLES
from faro.analysis.profile import profile
from faro.dataset.lock import read_lock
from faro.dataset.snapshot import snapshot_dir
from faro.pipeline import build, runner

from . import fixture
from .conftest import Built

pytestmark = pytest.mark.xdist_group("pipeline")

REPO = Path(__file__).resolve().parents[3]


def test_the_pipelines_clock_is_the_profiles(base: Built) -> None:
    profiled = profile(base.lock, base.root, TABLES, log=lambda _: None)

    with duckdb.connect(str(base.space.database), read_only=True) as con:
        rows = con.execute("select business_date, as_of from silver.clock").fetchall()

    assert rows == [(profiled.business_date, profiled.as_of)]
    assert rows == [(date(2026, 6, 16), datetime(2026, 6, 17, 6))]


def test_a_snapshot_without_settled_rows_has_no_clock(tmp_path: Path) -> None:
    files = {
        key: body
        for key, body in fixture.base().items()
        if "/year=" not in key or "/month=06/" in key
    }
    _, data_dir, lock = fixture.install_files(files, tmp_path)
    space = runner.workspace(data_dir, lock.snapshot_id)

    results = build.build(lock, snapshot_dir(data_dir, lock.snapshot_id), space)

    [failed] = [r for r in runner.failed(results) if r["status"] == "error"]
    assert runner.node_name(failed) == "silver_clock"
    assert "no daily table has settled rows" in failed["message"]


def test_the_pinned_snapshot_reads_as_of_the_profiles_instant_in_every_build() -> None:
    profiled = json.loads((REPO / "docs" / "analysis" / "profiling.json").read_text())
    pinned = read_lock(REPO / "dataset.lock").snapshot_id
    manifests = sorted((REPO / "docs" / "pipeline").glob(f"{pinned}-*.json"))

    assert profiled["snapshot_id"] == pinned == "b3b8b248f604ef9a"
    assert (profiled["business_date"], profiled["as_of"]) == (
        "2026-06-17",
        "2026-06-18 06:00:00",
    )
    assert manifests
    for path in manifests:
        assert json.loads(path.read_text())["clock"] == {
            "business_date": profiled["business_date"],
            "as_of": profiled["as_of"],
        }, path.name
