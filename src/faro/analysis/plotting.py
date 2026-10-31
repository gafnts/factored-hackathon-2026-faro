"""
The look every analysis figure shares, and SVG files that a rerun reproduces byte for byte.
"""

from collections.abc import Sequence
from pathlib import Path

import matplotlib
from plotnine import (
    element_blank,
    element_line,
    element_rect,
    element_text,
    ggplot,
    theme,
    theme_minimal,
)

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
SERIES = ("#2a78d6", "#eb6834")


class _Minimal(theme_minimal):
    def __init__(self) -> None:
        super().__init__()
        # Matplotlib ships DejaVu Sans, so every machine lays the text out alike; viewers fall back to their sans.
        self._rcParams["font.sans-serif"] = ["DejaVu Sans"]


def base_theme() -> theme:
    return _Minimal() + theme(
        text=element_text(color=SECONDARY, size=8),
        strip_text=element_text(color=INK, size=9, ha="left"),
        axis_title=element_text(color=SECONDARY, size=8),
        panel_grid_major=element_line(color=GRID, size=0.4),
        panel_grid_minor=element_blank(),
        plot_background=element_rect(fill=SURFACE, color=SURFACE),
        legend_position="top",
        legend_title=element_blank(),
    )


def percent(values: Sequence[float] | Sequence[str]) -> list[str]:
    return [f"{float(v):.0%}" for v in values]


def save(plot: ggplot, path: Path, width: float, height: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with matplotlib.rc_context(
        {"svg.hashsalt": "banking-agent", "svg.fonttype": "none"}
    ):
        plot.save(
            path,
            width=width,
            height=height,
            units="in",
            verbose=False,
            metadata={"Date": None},
        )
    return path
