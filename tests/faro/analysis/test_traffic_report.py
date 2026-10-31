"""
The traffic report keeps its projection apart from its measurements and says so (EVL-13), suppresses small
counts in every output (SEC-03), and a rerun writes the same bytes (OPS-07).
"""

import json
from pathlib import Path

import pytest

from faro.analysis.capacity import project
from faro.analysis.traffic import Traffic
from faro.analysis.traffic_report import _multiple, to_json, to_markdown, write


def test_json_keeps_the_projection_under_its_own_key(traffic_result: Traffic) -> None:
    data = json.loads(to_json(traffic_result, project(traffic_result)))

    assert set(data) == {"measurements", "projection"}
    assert "EVL-13" in data["projection"]["note"]
    assert "loads" not in data["measurements"]
    assert data["projection"]["assumptions"]


def test_markdown_labels_the_projection_before_any_projected_number(
    traffic_result: Traffic,
) -> None:
    text = to_markdown(traffic_result, project(traffic_result))
    measured, projected = text.split("## 9. Projection", 1)

    assert "## 8. Digital sessions" in measured
    assert "Assumption" not in measured
    assert projected.index("not a measurement (EVL-13)") < projected.index("### Load")


def test_suppresses_small_counts(traffic_result: Traffic) -> None:
    text = to_markdown(traffic_result, project(traffic_result))
    data = json.loads(to_json(traffic_result, project(traffic_result)))
    contacts = next(
        s for s in data["measurements"]["streams"] if s["name"] == "contacts"
    )

    assert "| Contacts | <10 |" in text
    assert contacts["rows"] == "<10"
    assert contacts["daily"]["first"] == "2025-01-01"
    assert set(contacts["daily"]["rows"]) == {0, "<10"}
    assert contacts["year"]["most"] == "<10"
    timing = data["measurements"]["contacts"]["overall"]["handle"]
    assert (timing["rows"], timing["mean"], timing["quantiles"]) == ("<10", None, None)


def test_rounds_multiples_to_two_figures() -> None:
    assert _multiple(1.66) == "1.7×"
    assert _multiple(2157.5) == "2,200×"
    assert _multiple(33.2) == "33×"
    assert _multiple(None).startswith("beyond")


@pytest.mark.filterwarnings("ignore::plotnine.exceptions.PlotnineWarning")
def test_writes_the_report_its_data_and_its_figures_the_same_twice(
    traffic_result: Traffic, tmp_path: Path
) -> None:
    projection = project(traffic_result)
    first = {
        p: p.read_bytes() for p in write(traffic_result, projection, tmp_path / "a")
    }
    second = {
        p: p.read_bytes() for p in write(traffic_result, projection, tmp_path / "b")
    }

    assert {p.name for p in first} == {
        "traffic.md",
        "traffic.json",
        "traffic-daily.svg",
        "traffic-hourly.svg",
        "traffic-capacity.svg",
    }
    assert sorted(first.values()) == sorted(second.values())
