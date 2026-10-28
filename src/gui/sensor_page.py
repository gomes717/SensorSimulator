"""One sensor's whole view: its own graphs, history, stats and commands.

Every sensor gets a page of its own — two canvases (glucose, food/exercise), a
Commands panel and a time-in-range panel — rather than sharing one graph that is
re-pointed at whichever sensor is selected. A message for sensor B therefore
lands in B's buffers and nowhere else, and a command sent from B's page can only
reach B. The cost is one set of matplotlib canvases per sensor; a page that is
not on screen defers its redraw until it is shown.

Right-clicking either graph opens a menu: **Show points** (a dot per received sample) and
**Export as CSV…** (every plotted series on one time column).
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QFileDialog,
    QMenu,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.commands_panel import CommandsPanel
from gui.glucose_graph import GlucoseGraph
from gui.range_stats import RangeStatsPanel
from gui.run_clock import RunClock
from models import graph_export

_FE_TITLE = "Food / Exercise"
_FE_TITLE_CSV = "Food log — report-only (CSV playback; does not drive glucose)"


class SensorPage(QWidget):
    """Graphs + commands + stats for one sensor (or the Model Only trace)."""

    show_points_toggled = pyqtSignal(bool)  # "Show points" was ticked / cleared in the menu

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
        self._show_points = False

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

        # The graphs take every spare pixel; the statistics and the command
        # buttons sit in a compact strip underneath.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        outer.addWidget(self.graphs, 1)
        outer.addWidget(self.stats)
        outer.addWidget(self.commands)
        self._attach_menus()
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
        """Show or hide the sample dots (without announcing it)."""
        self._show_points = show
        self.graph.set_show_points(show)

    @property
    def shows_points(self) -> bool:
        """Whether the received samples are drawn as dots."""
        return self._show_points

    def _on_points_toggled(self, show: bool) -> None:
        self.set_show_points(show)
        self.show_points_toggled.emit(show)

    # ------------------------------------------------------------------
    # The right-click menu
    # ------------------------------------------------------------------

    def graph_menu(self, parent: QWidget | None = None) -> QMenu:
        """The menu a right-click on a graph opens: Show points (a tick) and Export as CSV…."""
        menu = QMenu(parent if parent is not None else self)
        points = menu.addAction("Show points")
        points.setCheckable(True)
        points.setChecked(self._show_points)
        points.setToolTip("Draw each received sample as a dot on the glucose line")
        points.toggled.connect(self._on_points_toggled)
        export = menu.addAction("Export as CSV…")
        export.setToolTip("Save every plotted series of this sensor, on one time column")
        export.triggered.connect(lambda _checked=False: self.export_csv())
        return menu

    def _attach_menus(self) -> None:
        """Right-click on either canvas opens the menu (a rebuilt canvas is attached again)."""
        for canvas in (self.graph.canvas, self.graph.fe_canvas):
            canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            canvas.customContextMenuRequested.connect(
                lambda pos, canvas=canvas: self.graph_menu(canvas).exec(canvas.mapToGlobal(pos))
            )

    def export_csv(self, path: str | None = None) -> bool:
        """Write the plotted series to *path* (asked for in a save dialog when None). False if
        the dialog was cancelled or the file could not be written."""
        if path is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path, _filter = QFileDialog.getSaveFileName(
                self, "Export as CSV", f"glucose_{stamp}.csv", "CSV files (*.csv)"
            )
            if not path:
                return False
        b = self.graph.buf
        table = graph_export.rows(
            (b.graph_x, b.graph_y),
            (b.expected_x, b.expected_y),
            (b.food_ex_x, b.food_ex_carbs_y, b.food_ex_exercise_y),
        )
        try:
            graph_export.write_csv(path, table)
        except OSError as exc:
            QMessageBox.warning(self, "Export as CSV", f"The file could not be written: {exc}")
            return False
        return True

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
        self._attach_menus()

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
