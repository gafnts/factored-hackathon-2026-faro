"""
Reading exactly the locked files, and refusing a local copy that doesn't match the lock.
"""

from datetime import date
from pathlib import Path

import pytest

from faro.analysis.catalog import Table
from faro.analysis.source import (
    AnalysisError,
    check_local,
    connect,
    headers,
    partition_date,
    table_keys,
)
from faro.dataset.lock import Lock


def test_splits_the_lock_by_table(lock: Lock, tables: tuple[Table, ...]) -> None:
    branches, payments, receipts = tables

    assert table_keys(lock, branches) == ["branches.csv"]
    assert len(table_keys(lock, payments)) == 3
    assert len(table_keys(lock, receipts)) == 1


def test_reads_the_partition_date_from_the_file_name() -> None:
    assert partition_date("t/year=2026/month=06/day=17/t_20260617.csv") == date(
        2026, 6, 17
    )
    with pytest.raises(AnalysisError, match="partition date"):
        partition_date("t.csv")


def test_accepts_a_local_copy_that_matches(lock: Lock, root: Path) -> None:
    check_local(lock, root)


def test_refuses_a_missing_or_resized_file(lock: Lock, root: Path) -> None:
    (root / "branches.csv").unlink()
    (root / "receipts/year=2026/month=06/day=17/receipts_20260617.csv").write_text("x")

    with pytest.raises(AnalysisError, match="1 files missing, 1 with the wrong size"):
        check_local(lock, root)


def test_refuses_a_file_the_lock_doesnt_list_and_names_it(
    lock: Lock, root: Path
) -> None:
    late = root / "receipts/year=2026/month=06/day=18/receipts_20260618.csv"
    late.parent.mkdir(parents=True)
    late.write_text("receipt_id\n")

    with pytest.raises(AnalysisError, match="1 not in the lock") as refused:
        check_local(lock, root)

    assert "receipts/year=2026/month=06/day=18/receipts_20260618.csv" in str(
        refused.value
    )


def test_ignores_hidden_files_a_file_browser_leaves(lock: Lock, root: Path) -> None:
    (root / ".DS_Store").write_bytes(b"\x00")
    (root / "receipts" / ".DS_Store").write_bytes(b"\x00")

    check_local(lock, root)


def test_counts_header_versions_without_the_byte_order_mark(
    lock: Lock, root: Path, tables: tuple[Table, ...]
) -> None:
    branches, payments, _ = tables

    assert list(headers(root, table_keys(lock, branches))) == [
        ("branch_id", "code", "zone", "opened")
    ]
    assert len(headers(root, table_keys(lock, payments))) == 2


def test_views_read_every_value_as_text(
    lock: Lock, root: Path, tables: tuple[Table, ...]
) -> None:
    con = connect(root, tables, {t.name: table_keys(lock, t) for t in tables})

    rows = con.execute(
        "select payment_id, note from raw_payments order by payment_id, note nulls first"
    ).fetchall()

    assert ("P6", "late, again") in rows
    assert {type(value) for row in rows for value in row} <= {str, type(None)}
