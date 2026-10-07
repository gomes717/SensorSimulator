"""One sensor's whole view: its own graphs, history, stats and commands.

Every sensor gets a page of its own — two canvases (glucose, food/exercise), a
Commands panel and a time-in-range panel — rather than sharing one graph that is
re-pointed at whichever sensor is selected. A message for sensor B therefore
lands in B's buffers and nowhere else, and a command sent from B's page can only
reach B. The cost is one set of matplotlib canvases per sensor; a page that is
not on screen defers its redraw until it is shown.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QSplitter, QVBoxLayout, QWidget

from gui.commands_panel import CommandsPanel
from gui.glucose_graph import GlucoseGraph
from gui.range_stats import RangeStatsPanel
from gui.run_clock import RunClock

_FE_TITLE = "Food / Exercise"
_FE_TITLE_CSV = "Food log — report-only (CSV playback; does not drive glucose)"


class SensorPage(QWidget):
    """Graphs + commands + stats for one sensor (or the Model Only trace)."""

    show_points_toggled = pyqtSignal(bool)  # the "Show points" box was ticked / cleared

    def __init__(
        self,
        key: str | None,
        slot: int | None,
        thresholds: dict,
        view_window_s: float,
        clock: RunClock,
        parent=None,
        *,
        show_points: bool = False,
    ) -> None:
        super().__init__(parent)
        self.key = key  # the session user_id this page shows; None = the model / empty page
        self.slot = slot  # board slot on a multi-sensor board, else None
        self._thresholds = thresholds
        self._csv = False
        self._dirty = False

        self.graph = GlucoseGraph(
            thresholds,
            view_window_s,
            clock,
            on_redraw=self._update_stats,
            fe_title=lambda: _FE_TITLE_CSV if self._csv else _FE_TITLE,
        )
        self.graphs = QSplitter(Qt.Orientation.Vertical)
        self.graphs.addWidget(self.graph.canvas)
        self.graphs.addWidget(self.graph.fe_canvas)
        self.graphs.setSizes([320, 160])
        self.stats = RangeStatsPanel()
        self.commands = CommandsPanel()
        self.points_check = QCheckBox("Show points")
        self.points_check.setToolTip("Draw each received sample as a dot on the glucose line")
        self.points_check.toggled.connect(self._on_points_toggled)

        # The graphs take every spare pixel; the statistics and the command
        # buttons sit in a compact strip underneath.
        bottom = QHBoxLayout()
        bottom.addWidget(self.commands, 1)
        bottom.addWidget(self.points_check)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        outer.addWidget(self.graphs, 1)
        outer.addWidget(self.stats)
        outer.addLayout(bottom)
        self.set_show_points(show_points)

    # ------------------------------------------------------------------
    # Data in — each appends to THIS page's buffers and redraws if shown
    # ------------------------------------------------------------------

    def add_received(self, t: float, glucose: float) -> None:
        """A received glucose point."""
        self.graph.append_received(t, glucose)
        self._redraw_glucose_if_shown()

    def add_expected(self, t: float, glucose: float) -> None:
        """A point of the local model's expected line."""
        self.graph.append_expected(t, glucose)
        self._redraw_glucose_if_shown()

    def add_food_ex(self, t: float, carbs_rate: float, exercise_pct: float) -> None:
        """A food/exercise sample."""
        self.graph.append_food_ex(t, carbs_rate, exercise_pct)
        if self._shown():
            self.graph.redraw_food_ex()
        else:
            self._dirty = True

    def add_pisa(self, t_start_s: float, t_end_s: float) -> None:
        """Shade a PISA interval on this sensor's graph only."""
        self.graph.add_pisa_span(t_start_s, t_end_s)
        self._redraw_glucose_if_shown()

    def drop_expected(self) -> None:
        """Forget the expected line (this sensor is replaying a CSV, so no model runs)."""
        self.graph.clear_expected()
        self._redraw_glucose_if_shown()

    def clear(self) -> None:
        """Empty every series (a restart); the shared clock is re-anchored by the caller."""
        self.graph.clear()
        self.redraw()

    # ------------------------------------------------------------------
    # Appearance
    # ------------------------------------------------------------------

    def set_title(self, text: str) -> None:
        """The glucose graph's title."""
        self.graph.set_glucose_title(text)

    def set_show_points(self, show: bool) -> None:
        """Show or hide the sample dots (also ticks the box, without announcing it)."""
        self.points_check.blockSignals(True)
        self.points_check.setChecked(show)
        self.points_check.blockSignals(False)
        self.graph.set_show_points(show)

    def _on_points_toggled(self, show: bool) -> None:
        self.graph.set_show_points(show)
        self.show_points_toggled.emit(show)

    def set_csv(self, is_csv: bool) -> None:
        """A CSV-replaying sensor runs no model and takes no commands: hide the
        food/exercise graph and the Commands panel (food/exercise are report-only
        there and nothing the panel sends is meaningful)."""
        self._csv = is_csv
        self.graph.fe_canvas.setVisible(not is_csv)
        # The food/exercise graph normally carries the "Time (s)" axis label; with
        # it hidden the glucose graph has to carry it itself.
        self.graph.set_time_label(is_csv)
        self.graph.set_expected_visible(not is_csv)  # no model behind a replayed recording
        self.commands.setVisible(not is_csv)
        if self._shown():
            self.graph.redraw_food_ex()

    @property
    def is_csv(self) -> bool:
        """Whether the page is currently showing a CSV-replay sensor."""
        return self._csv

    def set_thresholds(self, thresholds: dict) -> None:
        """New range thresholds: recolour the bands, restats the view."""
        self._thresholds = thresholds
        self.graph.set_thresholds(thresholds)

    def set_view_window(self, seconds: float) -> None:
        """New rolling time window for the x-axis."""
        self.graph.set_view_window(seconds)

    def rebuild_for_theme(self) -> None:
        """Recreate the canvases so they pick up a new palette."""
        self.graph.rebuild_for_theme(self.graphs)

    # ------------------------------------------------------------------
    # Redraw
    # ------------------------------------------------------------------

    def _shown(self) -> bool:
        """Whether this page is the one on screen (visible within its window)."""
        return self.isVisibleTo(self.window())

    def _redraw_glucose_if_shown(self) -> None:
        if self._shown():
            self.graph.redraw_glucose()
        else:
            self._dirty = True

    def redraw(self) -> None:
        """Redraw both graphs now."""
        self._dirty = False
        self.graph.redraw_glucose()
        self.graph.redraw_food_ex()

    def showEvent(self, event: QShowEvent) -> None:  # Qt naming
        """Catch up on whatever arrived while this page was hidden."""
        super().showEvent(event)
        if self._dirty:
            self.redraw()

    def _update_stats(self) -> None:
        """Feed the range-metrics panel the glucose series *currently in view*."""
        g, b = self.graph, self.graph.buf
        series = g.in_view(b.graph_x, b.graph_y) or g.in_view(b.expected_x, b.expected_y)
        span_min = None
        if g.visible_xlim is not None:
            lo, hi = g.visible_xlim
            span_min = max(0.0, (hi - lo) / 60.0)
        self.stats.refresh(series, span_min, self._thresholds)
