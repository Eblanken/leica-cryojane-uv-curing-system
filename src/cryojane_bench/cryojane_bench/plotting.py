"""Shared chart styling for the bench notebooks."""

from __future__ import annotations

import matplotlib.pyplot as plt

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def apply_style() -> None:
    plt.rcParams.update({
        "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": MUTED,
        "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
        "axes.grid": True, "axes.axisbelow": True, "grid.color": GRID, "grid.linewidth": 0.8,
        "lines.linewidth": 2, "legend.frameon": False, "figure.dpi": 110,
        "axes.titlelocation": "left", "axes.titlecolor": INK,
    })
