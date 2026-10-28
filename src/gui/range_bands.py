"""The five clinical range bands (TBR2, TBR1, TIR, TAR1, TAR2) drawn behind a glucose plot.

Shared by the live glucose graph and the profile screen's 24 h preview so the two always colour
the same glucose the same way.
"""

from __future__ import annotations

from matplotlib.axes import Axes

# red = TBR2/TAR2 (out of range), yellow = TBR1/TAR1 (borderline), green = TIR
RED = "#ef6c6c"
YELLOW = "#ffd24d"
GREEN = "#6fcf7a"
# Band colours in the same 5-band order as the edges below: TBR2, TBR1, TIR, TAR1, TAR2.
BAND_COLORS = (RED, YELLOW, GREEN, YELLOW, RED)
_TOP_MG_DL = 600.0
_ALPHA = 0.09


def draw_range_bands(ax: Axes, thresholds: dict) -> list:
    """Shade the five bands on *ax* from *thresholds*; returns the patches (to remove on a redraw).

    axhspan patches don't feed the data limits, so the y-axis still autoscales to the data alone.
    """
    edges = [
        0.0,
        thresholds["tbr2_below"],
        thresholds["tbr1_below"],
        thresholds["tar1_above"],
        thresholds["tar2_above"],
        _TOP_MG_DL,
    ]
    return [
        ax.axhspan(lo, hi, color=color, alpha=_ALPHA, zorder=0)
        for lo, hi, color in zip(edges, edges[1:], BAND_COLORS)
    ]
