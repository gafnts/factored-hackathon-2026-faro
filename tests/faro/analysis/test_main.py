"""
The command line behind make analysis (PRB-05, OPS-07).
"""

from pathlib import Path
from typing import Any

import pytest

from faro import clock
from faro.analysis import __main__ as cli
from faro.analysis import profile as profiling
from faro.analysis.cards import CardSupport
from faro.analysis.catalog import Table
from faro.analysis.selection import Selection
from faro.analysis.traffic import Traffic
from faro.dataset.lock import Lock, write_lock


def options(tmp_path: Path) -> list[str]:
    return [
        "--lock",
        str(tmp_path / "dataset.lock"),
        "--data-dir",
        str(tmp_path / "data"),
        "--out",
        str(tmp_path / "analysis"),
    ]


@pytest.fixture
def locked(
    lock: Lock,
    tmp_path: Path,
    tables: tuple[Table, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_lock(tmp_path / "dataset.lock", lock)
    monkeypatch.setattr(cli, "TABLES", tables)


def test_writes_the_profile(locked: None, root: Path, tmp_path: Path) -> None:
    assert cli.main([*options(tmp_path), "profile"]) == 0
    assert (tmp_path / "analysis" / "profiling.md").is_file()
    assert (tmp_path / "analysis" / "profiling.json").is_file()


def test_asks_for_make_data_without_a_local_snapshot(
    locked: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([*options(tmp_path), "profile"]) == 1
    assert "run make data" in capsys.readouterr().err


def test_select_writes_the_profile_and_the_selection(
    locked: None,
    root: Path,
    tmp_path: Path,
    selection: Selection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clock, "SETTLED_DAYS", 0)
    monkeypatch.setattr(cli, "select", lambda *args, **kwargs: selection)

    assert cli.main([*options(tmp_path), "select"]) == 0
    written = {p.name for p in (tmp_path / "analysis").rglob("*") if p.is_file()}
    assert written == {
        "profiling.md",
        "profiling.json",
        "selection.md",
        "selection.json",
        "selection-f1-fields.svg",
        "selection-e2-learned.svg",
    }


CARD_SUPPORT_FILES = {
    "card-support.md",
    "card-support.json",
    "card-support-daily.svg",
    "card-support-declines.svg",
    "card-support-utilization.svg",
    "card-support-dates.svg",
}


@pytest.mark.filterwarnings("ignore::plotnine.exceptions.PlotnineWarning")
def test_cards_writes_the_profile_and_the_card_support_analysis(
    locked: None,
    root: Path,
    tmp_path: Path,
    card_result: CardSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clock, "SETTLED_DAYS", 0)
    monkeypatch.setattr(cli, "card_support", lambda *args, **kwargs: card_result)

    assert cli.main([*options(tmp_path), "cards"]) == 0
    written = {p.name for p in (tmp_path / "analysis").rglob("*") if p.is_file()}
    assert written == {"profiling.md", "profiling.json", *CARD_SUPPORT_FILES}


TRAFFIC_FILES = {
    "traffic.md",
    "traffic.json",
    "traffic-daily.svg",
    "traffic-hourly.svg",
    "traffic-capacity.svg",
}


@pytest.mark.filterwarnings("ignore::plotnine.exceptions.PlotnineWarning")
def test_traffic_writes_the_profile_and_the_traffic_analysis(
    locked: None,
    root: Path,
    tmp_path: Path,
    traffic_result: Traffic,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clock, "SETTLED_DAYS", 0)
    monkeypatch.setattr(cli, "traffic", lambda *args, **kwargs: traffic_result)

    assert cli.main([*options(tmp_path), "traffic"]) == 0
    written = {p.name for p in (tmp_path / "analysis").rglob("*") if p.is_file()}
    assert written == {"profiling.md", "profiling.json", *TRAFFIC_FILES}


@pytest.mark.filterwarnings("ignore::plotnine.exceptions.PlotnineWarning")
def test_all_profiles_once_and_writes_every_report(
    locked: None,
    root: Path,
    tmp_path: Path,
    selection: Selection,
    card_result: CardSupport,
    traffic_result: Traffic,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = []

    def counted(*args: Any, **kwargs: Any) -> profiling.Profile:
        profiles.append(args)
        return profiling.profile(*args, **kwargs)

    monkeypatch.setattr(clock, "SETTLED_DAYS", 0)
    monkeypatch.setattr(cli, "profile", counted)
    monkeypatch.setattr(cli, "select", lambda *args, **kwargs: selection)
    monkeypatch.setattr(cli, "card_support", lambda *args, **kwargs: card_result)
    monkeypatch.setattr(cli, "traffic", lambda *args, **kwargs: traffic_result)

    assert cli.main([*options(tmp_path), "all"]) == 0
    written = {p.name for p in (tmp_path / "analysis").rglob("*") if p.is_file()}
    assert len(profiles) == 1
    assert written == {
        "profiling.md",
        "profiling.json",
        "selection.md",
        "selection.json",
        "selection-f1-fields.svg",
        "selection-e2-learned.svg",
        *CARD_SUPPORT_FILES,
        *TRAFFIC_FILES,
    }


def test_traffic_needs_the_tables_it_reads(
    locked: None,
    root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clock, "SETTLED_DAYS", 0)

    assert cli.main([*options(tmp_path), "traffic"]) == 1
    assert (
        "the traffic analysis needs call_center_interactions" in capsys.readouterr().err
    )


def test_cards_needs_the_tables_it_reads(
    locked: None,
    root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clock, "SETTLED_DAYS", 0)

    assert cli.main([*options(tmp_path), "cards"]) == 1
    assert (
        "the card support analysis needs call_center_interactions"
        in capsys.readouterr().err
    )


def test_select_needs_the_tables_it_stages(
    locked: None,
    root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clock, "SETTLED_DAYS", 0)

    assert cli.main([*options(tmp_path), "select"]) == 1
    assert "the selection needs call_center_interactions" in capsys.readouterr().err


def test_select_needs_a_business_date(
    locked: None, root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Rows younger than SETTLED_DAYS don't count, so the fixture has no complete day.
    assert cli.main([*options(tmp_path), "select"]) == 1
    assert "no business date" in capsys.readouterr().err
