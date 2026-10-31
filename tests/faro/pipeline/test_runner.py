"""
dbt runs as a subprocess on the project in pipeline/, sends no usage events, and is summarized by check name and count.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from faro.dataset.lock import Lock, write_lock
from faro.pipeline import runner
from faro.pipeline.__main__ import main

from . import fixture


def test_the_project_parses_with_usage_stats_off(tmp_path: Path) -> None:
    space = runner.workspace(tmp_path, "0123456789abcdef")
    space.reset()

    assert runner.dbt(["parse"], space, {"snapshot_root": str(tmp_path)}) == []

    manifest = json.loads((space.target / "manifest.json").read_text())
    assert manifest["metadata"]["send_anonymous_usage_stats"] is False
    assert space.database == tmp_path / "pipeline" / "0123456789abcdef.duckdb"


def test_a_relative_data_dir_keeps_dbts_files_in_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    space = runner.workspace(Path("data"), "0123456789abcdef")
    space.reset()

    runner.dbt(["parse"], space, {"snapshot_root": str(tmp_path)})

    target = tmp_path / "data" / "pipeline" / "0123456789abcdef" / "target"
    assert (target / "manifest.json").is_file()
    assert not (runner.PROJECT / "data").exists()


def test_a_project_that_doesnt_parse_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: [\n")
    monkeypatch.setattr(runner, "PROJECT", project)
    space = runner.workspace(tmp_path, "0123456789abcdef")

    with pytest.raises(runner.PipelineError, match="dbt parse stopped"):
        runner.dbt(["parse"], space)


def test_reset_leaves_nothing_from_the_last_build(tmp_path: Path) -> None:
    space = runner.workspace(tmp_path, "0123456789abcdef")
    space.database.parent.mkdir(parents=True)
    space.database.write_text("old")
    space.target.mkdir(parents=True)

    space.reset()

    assert not space.database.exists()
    assert not space.work.exists()
    assert space.database.parent.is_dir()


def test_the_summary_names_each_check_that_didnt_pass(tmp_path: Path) -> None:
    results: list[dict[str, Any]] = [
        {"unique_id": "model.pipeline.bronze_customers", "status": "success"},
        {
            "unique_id": "test.pipeline.not_null_x.1a",
            "status": "warn",
            "failures": 1204,
        },
        {"unique_id": "test.pipeline.unique_y.2b", "status": "fail", "failures": 3},
        {"unique_id": "test.pipeline.unique_z.3c", "status": "pass", "failures": 0},
    ]

    lines = runner.summarize(results, tmp_path / "logs")

    assert lines == [
        "1 fail, 1 pass, 1 success, 1 warn",
        "  warn: not_null_x (1,204 rows)",
        "  fail: unique_y (3 rows)",
        f"dbt's messages are in {tmp_path / 'logs'}",
    ]
    assert runner.failed(results) == [results[2]]


@pytest.mark.xdist_group("pipeline")
def test_make_pipeline_builds_into_data_pipeline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lock_path, data_dir, lock = fixture.install("base", tmp_path)
    args = ["--lock", str(lock_path), "--data-dir", str(data_dir)]

    assert main([*args, "build"]) == 0

    database = tmp_path / "data" / "pipeline" / f"{lock.snapshot_id}.duckdb"
    assert f"Built {database}" in capsys.readouterr().out


def test_a_build_that_records_no_results_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lock_path, data_dir, _ = fixture.install("base", tmp_path)
    monkeypatch.setattr(runner, "dbt", lambda *args, **kwargs: [])
    args = ["--lock", str(lock_path), "--data-dir", str(data_dir)]

    assert main([*args, "build"]) == 1

    assert "dbt build recorded no results" in capsys.readouterr().err


def test_make_pipeline_refuses_a_missing_snapshot(
    tmp_path: Path, persona_bank: tuple[Lock, Path]
) -> None:
    lock, _ = persona_bank
    write_lock(tmp_path / "dataset.lock", lock)
    args = ["--lock", str(tmp_path / "dataset.lock")]
    args += ["--data-dir", str(tmp_path / "elsewhere")]

    assert main([*args, "build"]) == 1
