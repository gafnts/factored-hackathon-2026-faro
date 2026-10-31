"""
Bronze holds every row of every file, typed by its contract, with its file and snapshot (DML-02, DML-04); the checks that
stop the build pass, the warnings are counted in rows (DML-03); and a file that breaks its contract stops the build and
is named, without a value from it (SEC-03).
"""

import re
from pathlib import Path

import duckdb
import pytest

from faro.dataset.snapshot import snapshot_dir
from faro.pipeline import build, checks, contracts, runner
from faro.pipeline.__main__ import main

from . import fixture
from .conftest import Built

pytestmark = pytest.mark.xdist_group("pipeline")

WARNINGS = {
    "bronze_branches_country_dictionary_values": 1,
    "bronze_branches_geographic_zone_dictionary_values": 3,
    "bronze_call_center_interactions_detected_sentiment_dictionary_values": 1,
    "bronze_call_center_interactions_reason_category_dictionary_values": 3,
    "bronze_call_transcripts_duration_seconds_not_null": 1,
    "bronze_customers_country_dictionary_values": 6,
    "bronze_customers_document_type_dictionary_values": 6,
    "bronze_customers_registration_branch_id_references_branches": 1,
}


def test_bronze_holds_every_record_typed_by_its_contract(base: Built) -> None:
    with duckdb.connect(str(base.space.database), read_only=True) as con:
        for contract in contracts.build():
            columns = con.execute(
                "select column_name, data_type from information_schema.columns "
                "where table_schema = 'bronze' and table_name = ? order by ordinal_position",
                [contract.name],
            ).fetchall()
            assert columns == [
                *((c.name, c.type) for c in contract.columns),
                ("source_file", "VARCHAR"),
                ("snapshot_id", "VARCHAR"),
            ], contract.name
            read = con.execute(
                f"select source_file, count(*), any_value(snapshot_id) from bronze.{contract.name} "
                "group by source_file order by source_file"
            ).fetchall()
            counted = con.execute(
                "select key, records from checks.record_counts "
                "where key = ? or key like ? order by key",
                [f"{contract.name}.csv", f"{contract.name}/%"],
            ).fetchall()
            assert [(key, n) for key, n, _ in read] == counted
            assert {snapshot for *_, snapshot in read} == {base.lock.snapshot_id}


def test_the_checks_that_stop_the_build_pass_and_warnings_are_counted_in_rows(
    base: Built,
) -> None:
    assert runner.failed(base.results) == []
    warned = {
        runner.node_name(r): r["failures"]
        for r in base.results
        if r["status"] == "warn" and runner.node_name(r).startswith("bronze_")
    }

    assert warned == WARNINGS


def test_a_renamed_column_stops_the_build_before_a_file_is_read(tmp_path: Path) -> None:
    files = fixture.base()
    key = "transactions/year=2026/month=06/day=15/transactions_20260615.csv"
    files[key] = files[key].replace("merchant_name", "merchant", 1)
    _, data_dir, lock = fixture.install_files(files, tmp_path)
    space = runner.workspace(data_dir, lock.snapshot_id)

    with pytest.raises(runner.PipelineError) as stopped:
        build.build(lock, snapshot_dir(data_dir, lock.snapshot_id), space)

    assert f"{key}: added merchant; missing merchant_name" in str(stopped.value)
    assert not space.database.exists()


def test_a_value_that_doesnt_cast_stops_the_build_and_is_never_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = fixture.base()
    files["customers.csv"] = re.sub(
        r"(CLI-TEAM00000004,(?:[^,]*,){16})700,",
        r"\1SECRET-SCORE,",
        files["customers.csv"],
    )
    assert "SECRET-SCORE" in files["customers.csv"]
    lock_path, data_dir, lock = fixture.install_files(files, tmp_path)

    assert main(["--lock", str(lock_path), "--data-dir", str(data_dir), "build"]) == 1

    out = capsys.readouterr().out
    assert "error: bronze_customers" in out
    assert re.search(
        r"line 5 of .*customers\.csv: a value in credit_score isn't a INTEGER", out
    )
    assert "SECRET-SCORE" not in out


def test_a_row_filed_under_another_day_stops_the_build_and_names_its_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = fixture.base()
    key = "transactions/year=2026/month=06/day=15/transactions_20260615.csv"
    files[key] = files[key].replace(
        ",2026-06-15 21:07:33,2026-06-15,", ",2026-06-15 21:07:33,2026-06-14,", 1
    )
    lock_path, data_dir, lock = fixture.install_files(files, tmp_path)

    assert main(["--lock", str(lock_path), "--data-dir", str(data_dir), "build"]) == 1

    out = capsys.readouterr().out
    assert "fail: bronze_transactions_processed_on_its_day (1 rows)" in out
    assert f"    {key}" in out


def test_rows_duckdb_read_apart_from_the_records_counted_stop_the_build(
    tmp_path: Path,
) -> None:
    lock_path, data_dir, lock = fixture.install("base", tmp_path)
    root = snapshot_dir(data_dir, lock.snapshot_id)
    space = runner.workspace(data_dir, lock.snapshot_id)
    records = checks.count_records(root, [f.key for f in lock.files])
    miscounted = "transactions/year=2026/month=06/day=15/transactions_20260615.csv"
    records[miscounted] += 1
    space.reset()
    checks.load(space.database, lock, records)

    results = runner.dbt(["build"], space, build.variables(lock, root))

    named = checks.failing_files(space.database, results)
    failed = {
        runner.node_name(r): named.get(r["unique_id"]) for r in runner.failed(results)
    }
    assert failed == {"bronze_transactions_rows_match_the_records": [miscounted]}


def test_records_count_a_quoted_line_break_once_in_and_out_of_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.csv").write_text('﻿id,text\n1,"two\nlines"\n2,x\n')
    (tmp_path / "b.csv").write_text("﻿id,text\n")

    assert checks.count_records(tmp_path, ["a.csv", "b.csv"]) == {
        "a.csv": 2,
        "b.csv": 0,
    }
    monkeypatch.setattr(checks, "PARALLEL_FROM", 0)
    assert checks.count_records(tmp_path, ["a.csv", "b.csv"], workers=2) == {
        "a.csv": 2,
        "b.csv": 0,
    }


def test_duckdbs_read_error_is_reduced_to_its_file_line_column_and_type() -> None:
    message = (
        "Runtime Error in model bronze_x\n  Conversion Error: CSV Error on Line: 3\n"
        '  Original Line: secret-value,y\n  Error when converting column "a". Could not '
        "convert string \"secret-value\" to 'INTEGER'\n\n  file = /data/x/x_1.csv\n"
    )
    width = (
        "CSV Error on Line: 7\nOriginal Line: 1,2,3\nExpected Number of Columns: 2 Found: 3\n"
        "  file = /data/x.csv\n"
    )

    assert (
        runner.reason(message)
        == "line 3 of /data/x/x_1.csv: a value in a isn't a INTEGER"
    )
    assert (
        runner.reason(width)
        == "line 7 of /data/x.csv: 3 columns where the contract has 2"
    )
    assert runner.reason("CSV Error on Line: 2\nfile = /x.csv\n") == (
        "line 2 of /x.csv doesn't read as its contract says"
    )
    assert runner.reason("Binder Error: no column") is None
    assert runner.reason(None) is None


def test_a_header_in_another_order_is_named_so() -> None:
    assert checks._difference(["a", "b"], ["b", "a"]) == (
        "the contract's columns in another order"
    )
    assert checks._difference(["a", "b"], ["a", "b", "c"]) == "added c"
