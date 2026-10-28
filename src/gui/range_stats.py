"""The live "Time in range — current view" panel below the glucose graph:
time in range, time below range (levels 1 and 2) and time above range (levels 1 and 2)
as time (h:mm), plus mean and variance, for whatever series is currently plotted.

Pulled out of :class:`MainWindow` (issue 18). The owner feeds it the in-view
series + the window span; this widget owns the 9-cell grid and the formatting.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QGroupBox, QLabel

from models import cgm_metrics

_CELLS = (
    ("tir", "Time in range (TIR)"),
    ("tbr", "Time below range (TBR)"),
    ("tbr1", "Time below range, level 1 (TBR1)"),
    ("tbr2", "Time below range, level 2 (TBR2)"),
    ("tar", "Time above range (TAR)"),
    ("tar1", "Time above range, level 1 (TAR1)"),
    ("tar2", "Time above range, level 2 (TAR2)"),
    ("mean", "Mean"),
    ("variance", "Variance"),
)


class RangeStatsPanel(QGroupBox):
    def __init__(self, parent=None) -> None:
        super().__init__("Time in range — current view (h:mm)", parent)
        grid = QGridLayout(self)
        self.labels: dict[str, QLabel] = {}
        for i, (key, caption) in enumerate(_CELLS):
            row, col = divmod(i, 5)
            cap = QLabel(caption)
            cap.setWordWrap(True)  # the full names are long: wrap instead of widening the window
            # Sized up for readability (captions +1 pt, values +5 pt bold).
            # A QFont tweak, not setStyleSheet: any stylesheet on a widget
            # routes its rendering through QStyleSheetStyle, which does not
            # reliably re-resolve text color from a later app.setPalette()
            # (see gui/theme.py) — these captions went near-invisible in light
            # mode and stayed wrong after switching back to dark.
            font = cap.font()
            font.setPointSize(font.pointSize() + 1)
            cap.setFont(font)
            val = QLabel("—")
            value_font = val.font()
            value_font.setPointSize(value_font.pointSize() + 5)
            value_font.setBold(True)
            val.setFont(value_font)
            self.labels[key] = val
            # Captions and values on shared grid rows, so a caption that wraps to more lines
            # pushes the whole row of values down together instead of misaligning one column.
            grid.addWidget(cap, 2 * row, col, Qt.AlignmentFlag.AlignBottom)
            grid.addWidget(val, 2 * row + 1, col)

    def refresh(self, series: list[float], span_minutes: float | None, thresholds: dict) -> None:
        """Recompute from *series* (the in-view glucose values) over *span_minutes*.

        TIR/TBR/TAR are shown as time in each band (h:mm) over the visible
        window, not a percentage (issue 13).
        """
        m = cgm_metrics.compute(
            series,
            tbr2_below=thresholds["tbr2_below"],
            tbr1_below=thresholds["tbr1_below"],
            tar1_above=thresholds["tar1_above"],
            tar2_above=thresholds["tar2_above"],
            span_minutes=span_minutes,
        )
        if m.n == 0:
            for val in self.labels.values():
                val.setText("—")
            return
        fmt = cgm_metrics.fmt_hm
        self.labels["tir"].setText(fmt(m.tir_min))
        self.labels["tbr"].setText(fmt(m.tbr_min))
        self.labels["tbr1"].setText(fmt(m.tbr1_min))
        self.labels["tbr2"].setText(fmt(m.tbr2_min))
        self.labels["tar"].setText(fmt(m.tar_min))
        self.labels["tar1"].setText(fmt(m.tar1_min))
        self.labels["tar2"].setText(fmt(m.tar2_min))
        self.labels["mean"].setText(f"{m.mean:.0f}")
        self.labels["variance"].setText(f"{m.variance:.0f}")
