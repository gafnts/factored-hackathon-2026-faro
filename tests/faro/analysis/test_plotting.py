"""
Every analysis figure shares one look, and a rerun writes the same bytes (OPS-07).
"""

from pathlib import Path

from faro.analysis.figures import field_populations
from faro.analysis.plotting import percent, save
from faro.analysis.selection import Selection


def test_a_rerun_writes_the_same_svg(selection: Selection, tmp_path: Path) -> None:
    plot = field_populations(selection)
    first = save(plot, tmp_path / "a" / "figure.svg", 6, 4).read_bytes()
    second = save(plot, tmp_path / "b" / "figure.svg", 6, 4).read_bytes()

    assert first == second
    assert b"<dc:date>" not in first


def test_reads_shares_as_whole_percentages() -> None:
    assert percent([0, 0.25, 0.9, 1]) == ["0%", "25%", "90%", "100%"]
