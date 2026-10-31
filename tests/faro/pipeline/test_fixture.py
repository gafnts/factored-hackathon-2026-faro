"""
The fixture is team-generated, labeled so (SEC-02), and shaped like the delivery: every table, the contracts' headers
with a byte order mark, values that read with the contracts' types, and a business clock the rule computes (DML-06).
"""

import csv
import re
from datetime import date, datetime
from pathlib import Path
from string import Template

import duckdb
import pytest

from faro.analysis.catalog import TABLES
from faro.analysis.profile import profile
from faro.analysis.source import check_local, headers, table_keys
from faro.dataset.lock import read_lock
from faro.dataset.snapshot import snapshot_dir
from faro.pipeline import contracts
from faro.split import held_out

from . import fixture


@pytest.mark.parametrize("version", fixture.VERSIONS)
def test_the_committed_fixture_is_the_generators(version: str) -> None:
    files = fixture.VERSIONS[version]()
    lock = read_lock(fixture.ROOT / version / fixture.LOCK)

    assert dict(fixture.committed(version)) == {
        key: body.encode() for key, body in files.items()
    }, "run python -m tests.faro.pipeline.fixture"
    assert lock == fixture.lock_for(files)
    assert (
        (fixture.ROOT / version / fixture.LOCK)
        .read_text()
        .startswith(fixture.LOCK_HEADER)
    )


@pytest.mark.parametrize("version", fixture.VERSIONS)
def test_every_id_and_contact_is_labeled_team_generated(version: str) -> None:
    text = "".join(fixture.VERSIONS[version]().values())

    ids = re.findall(r"(?<![\w-])[A-Z]{3}-[A-Z0-9-]+", text)
    assert ids
    assert all("TEAM" in i for i in ids)
    emails = re.findall(r"[\w.]+@[\w.-]+", text)
    assert emails
    assert all(e.endswith("@team-generated.example") for e in emails)


def test_every_table_has_files_whose_headers_are_its_contract(tmp_path: Path) -> None:
    _, data_dir, lock = fixture.install("base", tmp_path)
    root = snapshot_dir(data_dir, lock.snapshot_id)

    check_local(lock, root)
    for contract in contracts.build():
        keys = table_keys(lock, contract.table)
        assert keys, contract.name
        assert list(headers(root, keys)) == [tuple(c.name for c in contract.columns)]
        assert all(
            (root / key).read_bytes().startswith(b"\xef\xbb\xbf") for key in keys
        )


def test_every_file_reads_with_its_contracts_types(tmp_path: Path) -> None:
    _, data_dir, lock = fixture.install("base", tmp_path)
    root = snapshot_dir(data_dir, lock.snapshot_id)

    for contract in contracts.build():
        read = Template(contracts.location(contract)).substitute(root=root)
        counted = duckdb.sql(f"select count(*) from {read}").fetchone()
        records = 0
        for key in table_keys(lock, contract.table):
            with (root / key).open(encoding="utf-8-sig", newline="") as fh:
                records += sum(1 for _ in csv.reader(fh)) - 1
        assert counted == (records,), contract.name


def test_the_base_reads_as_of_the_day_its_last_complete_day_closes(
    tmp_path: Path,
) -> None:
    _, data_dir, lock = fixture.install("base", tmp_path)

    profiled = profile(
        lock, snapshot_dir(data_dir, lock.snapshot_id), TABLES, log=lambda _: None
    )

    assert profiled.business_date == date(2026, 6, 16)
    assert profiled.as_of == datetime(2026, 6, 17, 6)
    by_name = {t.name: t for t in profiled.tables}
    assert all(t.duplicates.exact == 0 for t in profiled.tables)
    assert all(
        t.arrival.misfiled == 0 and t.arrival.undated == 0
        for t in profiled.tables
        if t.arrival
    )
    assert by_name["customers"].orphans["registration_branch_id"] == 1


def test_the_base_holds_customers_on_both_sides_of_the_split() -> None:
    customers = {
        row["customer_id"] for row in fixture.CUSTOMERS if row.get("customer_id")
    }

    assert {held_out(c) for c in customers} == {True, False}
