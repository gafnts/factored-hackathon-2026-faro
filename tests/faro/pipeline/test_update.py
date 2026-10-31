"""
An update is handled correctly (DML-06): the fixture's second version is built on the machine that built the first, as
the next delivery would be, and held to a clean build of it; each broken variant stops the build and names its file.
The six checks are ADR-0006's, under Update correctness.
"""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from faro.dataset.snapshot import snapshot_dir
from faro.pipeline import export, runner
from faro.pipeline.__main__ import main

from . import fixture
from .conftest import Built, build_version

pytestmark = pytest.mark.xdist_group("pipeline")


@dataclass(frozen=True)
class Update:
    first: Built
    second: Built
    clean: Built


@pytest.fixture(scope="module")
def update(tmp_path_factory: pytest.TempPathFactory) -> Update:
    machine = tmp_path_factory.mktemp("machine")
    return Update(
        first=build_version("base", machine),
        second=build_version("update", machine),
        clean=build_version("update", tmp_path_factory.mktemp("clean")),
    )


def _contents(built: Built) -> dict[str, list[tuple[Any, ...]]]:
    with duckdb.connect(str(built.space.database), read_only=True) as con:
        tables = con.execute(
            "select table_schema, table_name from information_schema.tables "
            "order by all"
        ).fetchall()
        return {
            f"{schema}.{name}": sorted(
                con.execute(f'select * from "{schema}"."{name}"').fetchall(), key=repr
            )
            for schema, name in tables
        }


def _hashes(built: Built) -> dict[str, str]:
    return {
        name: hashlib.sha256(repr(rows).encode()).hexdigest()
        for name, rows in _contents(built).items()
    }


def _one(built: Built, sql: str, *args: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(str(built.space.database), read_only=True) as con:
        return con.execute(sql, list(args)).fetchall()


def _ids(built: Built, sql: str) -> set[str]:
    return {str(row[0]) for row in _one(built, sql)}


def test_building_the_same_version_twice_gives_the_same_content_hashes(
    base: Built, update: Update, tmp_path: Path
) -> None:
    assert _hashes(update.first) == _hashes(base)

    _, again = export.export(update.first.lock, update.first.data_dir, tmp_path / "a")
    _, once = export.export(base.lock, base.data_dir, tmp_path / "b")
    assert again["content"] == once["content"]
    assert again == once


def test_the_second_build_is_a_clean_build_of_the_second_version(
    update: Update,
) -> None:
    assert runner.failed(update.second.results) == []
    assert update.second.space.database != update.first.space.database

    second = _contents(update.second)
    assert {name.split(".")[0] for name in second} >= {
        "bronze",
        "silver",
        "gold",
        "checks",
    }
    assert second == _contents(update.clean)

    snapshots = {
        row[0]
        for name in second
        if name.startswith("bronze.")
        for row in _one(update.second, f"select distinct snapshot_id from {name}")
    }
    assert snapshots == {update.second.lock.snapshot_id}
    read = {
        row[0]
        for name in second
        if name.startswith("bronze.")
        for row in _one(update.second, f"select distinct source_file from {name}")
    }
    assert read == {f.key for f in update.second.lock.files}


def test_the_business_date_moves_only_when_the_new_day_is_complete(
    update: Update,
) -> None:
    clock = "select business_date, as_of from gold.metadata"
    assert _one(update.first, clock) == [(date(2026, 6, 16), datetime(2026, 6, 17, 6))]
    assert _one(update.second, clock) == [(date(2026, 6, 17), datetime(2026, 6, 18, 6))]
    assert fixture.NEXT_DAY in _ids(
        update.second, "select source_file from bronze.transactions"
    )

    transactions = _ids(
        update.second, "select transaction_id from silver.card_transactions"
    )
    assert "TRX-TEAM0000000000000308" in transactions
    assert "TRX-TEAM0000000000000309" in _ids(
        update.second, "select transaction_id from bronze.transactions"
    )
    assert "TRX-TEAM0000000000000309" not in transactions
    customers = _ids(update.second, "select customer_id from silver.customers")
    assert "CLI-TEAM00000011" in customers
    assert "CLI-TEAM00000010" not in customers


def test_the_changed_card_keeps_its_delivered_values_and_is_flagged(
    update: Update,
) -> None:
    card = (
        "select product_status, last_updated, updated_after_as_of "
        "from silver.cards where product_id = ?"
    )
    assert _one(update.first, card, fixture.CHANGED_CARD) == [
        ("Active", datetime(2025, 1, 1, 10), False)
    ]
    assert _one(update.second, card, fixture.CHANGED_CARD) == [
        ("Blocked", datetime(2026, 6, 19, 9), True)
    ]
    assert _one(
        update.second,
        "select product_status, updated_after_as_of from gold.cards where card_id = ?",
        fixture.CHANGED_CARD,
    ) == [("Blocked", True)]


@pytest.mark.parametrize(
    ("variant", "key", "problem"),
    [
        ("update-extra-column", fixture.NEXT_DAY, "added team_note"),
        (
            "update-renamed-column",
            fixture.COMPLETED_DAY,
            "added complaint_status; missing status",
        ),
    ],
)
def test_each_broken_variant_stops_the_build_and_names_its_bad_file(
    variant: str,
    key: str,
    problem: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lock_path, data_dir, lock = fixture.install(variant, tmp_path)

    assert main(["--lock", str(lock_path), "--data-dir", str(data_dir), "build"]) == 1

    err = capsys.readouterr().err
    assert "1 files' headers aren't their contracts'" in err
    assert f"{key}: {problem}" in err
    assert not runner.workspace(data_dir, lock.snapshot_id).database.exists()
    assert (snapshot_dir(data_dir, lock.snapshot_id) / key).is_file()


def test_the_export_is_the_second_versions_with_nothing_left_from_the_first(
    update: Update, tmp_path: Path
) -> None:
    first_dir, first = export.export(
        update.first.lock, update.first.data_dir, tmp_path / "first"
    )
    directory, second = export.export(
        update.second.lock, update.second.data_dir, tmp_path / "second"
    )
    clean_dir, clean = export.export(
        update.clean.lock, update.clean.data_dir, tmp_path / "clean"
    )

    assert second == clean
    assert sorted(p.relative_to(directory) for p in directory.rglob("*.gz")) == sorted(
        Path(key) for key in second["objects"]
    )
    for key in second["objects"]:
        assert (directory / key).read_bytes() == (clean_dir / key).read_bytes()
    assert directory != first_dir
    assert second["snapshot"] == update.second.lock.snapshot_id
    assert second["clock"] == {
        "business_date": "2026-06-17",
        "as_of": "2026-06-18 06:00:00",
    }
    assert first["content"] != second["content"]

    with duckdb.connect(str(update.second.space.database), read_only=True) as con:
        items = export.read_items(con, update.second.stamp)
    keys = {(i["pk"], i["sk"]) for i in items}
    assert ("CLI-TEAM00000011", "CUSTOMER") in keys
    assert ("CLI-TEAM00000003", "TXN#TRX-TEAM0000000000000308") in keys
    for left_by_the_window in (312, 313):
        assert (
            "CLI-TEAM00000003",
            f"TXN#TRX-TEAM{left_by_the_window:016d}",
        ) not in keys
    [metadata] = [i for i in items if i["kind"] == "metadata"]
    assert metadata["stamp"] == update.second.stamp
