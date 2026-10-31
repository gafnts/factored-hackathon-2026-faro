"""
The reports publish aggregates only, with small row counts suppressed (SEC-03).
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from faro import clock
from faro.analysis import profile as profiling
from faro.analysis.attribution import Attribution, Cutoff
from faro.analysis.catalog import Table
from faro.analysis.profile import Profile, profile
from faro.analysis.report import count, share, to_json, to_markdown, write
from faro.dataset.lock import Lock


@pytest.fixture
def result(
    lock: Lock, root: Path, tables: tuple[Table, ...], monkeypatch: pytest.MonkeyPatch
) -> Profile:
    monkeypatch.setattr(clock, "SETTLED_DAYS", 0)
    monkeypatch.setitem(profiling.COUNTED, "branches", ("zone",))
    return profile(lock, root, tables, log=lambda _: None)


def test_suppresses_counts_under_ten() -> None:
    assert [count(n) for n in (0, 1, 9, 10, 12345)] == [
        "0",
        "<10",
        "<10",
        "10",
        "12,345",
    ]
    assert share(3, 100) == "<10"
    assert share(0, 100) == "0"
    assert share(20, 100) == "20 (20.00%)"


def test_json_suppresses_row_counts_but_not_files_or_days(result: Profile) -> None:
    data = json.loads(to_json(result))
    payments = next(t for t in data["tables"] if t["name"] == "payments")

    assert data["business_date"] == "2026-06-14"
    assert data["as_of"] == "2026-06-15 00:00:00"
    assert payments["rows"] == "<10"
    assert payments["files"] == 3
    assert payments["arrival"]["lag_p99"] == 2
    assert payments["arrival"]["lags"]["same day"] == "<10"
    assert payments["arrival"]["lags"]["8 to 30 days late"] == 0


def test_markdown_lists_what_the_contracts_must_handle(result: Profile) -> None:
    markdown = to_markdown(result)

    assert "**2026-06-14**" in markdown
    assert "as of **2026-06-15 00:00:00**" in markdown
    assert "`Urbana`" in markdown
    assert "- `approved`: `False`, `True`, `maybe`" in markdown
    assert "`payments.branch_id` | <10" in markdown
    assert "## Languages" in markdown


def test_writes_both_reports(result: Profile, tmp_path: Path) -> None:
    markdown, data = write(result, tmp_path / "analysis")

    assert markdown.read_text().startswith("# Snapshot profile")
    assert json.loads(data.read_text())["snapshot_id"] == result.snapshot_id


def test_reports_attribution_and_cutoffs(result: Profile) -> None:
    traced = replace(
        result,
        attribution=Attribution(
            contacts=686_296,
            reasons=6,
            reasons_matching_category=686_296,
            intents=("consulta_general",),
            transcripts=171_321,
            customer_texts=42,
            texts_under_every_reason=42,
            mentions=548_680,
            mentions_found=3_571,
            mentions_owned=0,
            complaints=67_095,
            complaints_linked=0,
        ),
        cutoffs=(Cutoff("transactions", "Mexico", 5, "06:00:00", "06:00:00"),),
    )

    markdown = to_markdown(traced)
    data = json.loads(to_json(traced))

    assert "## Contact attribution" in markdown
    assert "3,571 (0.65%) exist in `products`; 0 belong to the caller" in markdown
    assert "| `transactions` | Mexico | <10 | 06:00:00 | 06:00:00 |" in markdown
    assert data["cutoffs"][0]["rows"] == "<10"
    assert data["attribution"]["customer_texts"] == 42
