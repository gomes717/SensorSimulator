"""The two stacked matplotlib panels — glucose (received + expected + range
bands + PISA shading) and food/exercise — as one deep module.

Pulled out of :class:`MainWindow` (issue 18): ~380 lines of figure
construction, theming, range-band math, per-category recolouring, rolling-window
time-axis sync, y-limit fitting and redraw bookkeeping lived on the god object,
tangled with its BLE + engine state. Here they sit behind a small interface.

Each sensor page owns one of these outright — its own buffers, its own canvases —
and shares only the :class:`RunClock` timeline with the others. Appends go
through :meth:`append_received` / :meth:`append_expected` / :meth:`append_food_ex`
and land in the artists on the next :meth:`redraw_glucose`.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.axes import Axes
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QSplitter

from gui.run_clock import RunClock
from models import alerts

# red = TBR2/TAR2 (out of range), yellow = TBR1/TAR1 (borderline), green = TIR
_RED = "#ef6c6c"
_YELLOW = "#ffd24d"
_GREEN = "#6fcf7a"
_LINE_COLORS = {"r": _RED, "y": _YELLOW, "g": _GREEN}
# Clinical range band colors (translucent), same 5-band order: TBR2, TBR1, TIR, TAR1, TAR2.
_BAND_COLORS = (_RED, _YELLOW, _GREEN, _YELLOW, _RED)
# Left/right are shared so the two stacked plot boxes line up on the time
# axis; top/bottom differ — the glucose graph only reserves room for its
# legend, the food/exercise graph also carries the "Time (s)" label.
# Left/right margins are in inches too, and shared by both graphs so their time
# axes line up: just enough for the y tick labels + axis label on the left and the
# food/exercise graph's right-hand axis label, so the plots use the rest of the width.
_LEFT_IN = 0.85
_RIGHT_IN = 0.65
# Food/exercise: tick labels, then the "Time (s)" label, then the legend below it.
_FOODEX_TOP_IN = 0.30
_FOODEX_BOTTOM_IN = 0.64
# Floors for the canvases, so a short window cannot squeeze the plot so far that the
# axis label and legend (fixed-size text) run into each other.
_GLUCOSE_MIN_HEIGHT_PX = 230
_FOODEX_MIN_HEIGHT_PX = 230
# The glucose graph's top and bottom margins are in inches, not fractions, so the
# legend and the axis label keep their room however tall the graph is stretched:
# the bottom holds the tick labels and the legend, plus a "Time (s)" row when the
# food/exercise graph below (which normally carries that label) is hidden.
_GLUCOSE_TOP_IN = 0.32
_GLUCOSE_BOTTOM_IN = 0.50
_GLUCOSE_BOTTOM_LABELLED_IN = 0.64
# y-axis when the glucose graph has no data yet (mg/dL)
_EMPTY_YLIM = (40.0, 200.0)
# The line colour is the alert level (models.alerts): critical red, warning yellow.
_CATEGORY_OF_LEVEL = {
    alerts.Level.CRITICAL: "r",
    alerts.Level.WARNING: "y",
    alerts.Level.NORMAL: "g",
}


@dataclass
class _Buffers:
    """The live plot series of one graph. Replaced wholesale by clear()."""

    graph_x: list[float] = field(default_factory=list)
    graph_y: list[float] = field(default_factory=list)
    expected_x: list[float] = field(default_factory=list)
    expected_y: list[float] = field(default_factory=list)
    food_ex_x: list[float] = field(default_factory=list)
    food_ex_carbs_y: list[float] = field(default_factory=list)
    food_ex_exercise_y: list[float] = field(default_factory=list)
    # PISA-shaded intervals (t_start_s, t_end_s) — this graph's sensor only, so a
    # fault inserted for one sensor never shades another's graph.
    pisa_spans: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class _GlucosePlot:
    """Artists for the glucose figure (the canvas is kept on GlucoseGraph)."""

    figure: Figure
    ax: Axes
    fg: str
    expected_line: Line2D
    seg_lines: dict[str, Line2D]
    point_lines: dict[str, Line2D]  # the received samples as dots, one line per range category
    legend: object
    range_bands: list = field(default_factory=list)


@dataclass
class _FoodExPlot:
    """Artists for the food/exercise figure."""

    figure: Figure
    ax: Axes
    ax2: Axes
    carbs_line: Line2D
    exercise_line: Line2D


def _foodex_margins(figure: Figure) -> dict[str, float]:
    """top/bottom/left/right for the food/exercise figure, top and bottom in inches so the
    axis label and the legend keep clear of each other at any height."""
    height_in = max(figure.get_figheight(), 0.5)
    bottom = min(_FOODEX_BOTTOM_IN / height_in, 0.5)
    top = max(1.0 - _FOODEX_TOP_IN / height_in, bottom + 0.15)
    return {"bottom": bottom, "top": top, **_side_margins(figure)}


def _side_margins(figure: Figure) -> dict[str, float]:
    """left/right subplot fractions that keep the fixed inch margins at any width."""
    width_in = max(figure.get_figwidth(), 2.0)
    return {"left": min(_LEFT_IN / width_in, 0.3), "right": 1.0 - min(_RIGHT_IN / width_in, 0.3)}


class GlucoseGraph:
    """Owns the glucose + food/exercise canvases and every draw decision for them."""

    def __init__(
        self,
        thresholds: dict,
        view_window_s: float,
        clock: RunClock,
        *,
        on_redraw: Callable[[], None] | None = None,
        fe_title: Callable[[], str] | None = None,
    ) -> None:
        self.thresholds = thresholds
        self.view_window_s = view_window_s
        # The x-axis is *simulated* time (see RunClock): the axis, the rolling
        # view window and the h:mm metrics all read in sim-time, not wall-clock
        # (user report 2026-09-08).
        self.clock = clock
        self._on_redraw = on_redraw
        self._fe_title = fe_title or (lambda: "Food / Exercise")

        self.buf = _Buffers()
        # Drawn matplotlib patches for buf.pisa_spans (graph time), rebuilt
        # wholesale on every _draw_pisa_spans() call.
        self.pisa_patches: list = []
        self.visible_xlim: tuple[float, float] | None = None
        self._show_points = False
        self._show_expected = True
        self._time_label = False

        self.canvas: FigureCanvas = None  # set by _build_glucose
        self.fe_canvas: FigureCanvas = None  # set by _build_food_ex
        self._build_glucose()
        self._build_food_ex()

    # The live series (self.buf, a _Buffers) and the two Axes are the panel's
    # public read surface — the owner appends to buf.* and the hardware harnesses
    # poll it through MainWindow's compatibility properties.
    @property
    def ax(self) -> Axes:
        return self._g.ax

    @property
    def fe_ax(self) -> Axes:
        return self._fe.ax

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_glucose(self) -> None:
        """Glucose figure: a faint "received" base, one colored overlay per range
        category, a dashed "expected" line and the 5 range bands.

        Colors come from the live *application* palette (not a widget's) so a
        rebuild right after a theme switch sees the new colors immediately.
        """
        palette = QApplication.instance().palette()
        bg = palette.color(QPalette.ColorRole.Window).name()
        fg = palette.color(QPalette.ColorRole.WindowText).name()
        accent = palette.color(QPalette.ColorRole.Highlight).name()

        figure = Figure(facecolor=bg)
        self.canvas = FigureCanvas(figure)
        self.canvas.setMinimumHeight(_GLUCOSE_MIN_HEIGHT_PX)
        self.canvas.mpl_connect("resize_event", lambda _e: self._apply_glucose_margins())
        ax = figure.add_subplot(111, facecolor=bg)
        ax.set_title("Select a user", color=fg)
        ax.set_ylabel("Glucose (mg/dL)", color=fg)
        ax.tick_params(colors=fg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        ax.grid(True, color=fg, alpha=0.15)
        seg_labels = {"g": "In range", "y": "Borderline", "r": "Low / High"}
        seg_lines = {
            cat: ax.plot(
                [], [], lw=1.7, color=color, solid_capstyle="round", label=seg_labels[cat]
            )[0]
            for cat, color in _LINE_COLORS.items()
        }
        # The received samples as dots (off by default; see set_show_points). One
        # marker-only line per range category so each dot takes its band's colour.
        point_lines = {
            cat: ax.plot(
                [], [], linestyle="none", marker="o", ms=3.5, color=color, visible=self._show_points
            )[0]
            for cat, color in _LINE_COLORS.items()
        }
        (expected_line,) = ax.plot(
            [], [], lw=1.5, color=accent, linestyle="--", alpha=0.7, label="Expected (model)"
        )
        legend = self._make_legend(figure, ax, fg, seg_lines, expected_line)
        if self._time_label:
            ax.set_xlabel("Time (s)", color=fg)
        expected_line.set_visible(self._show_expected)
        self._g = _GlucosePlot(figure, ax, fg, expected_line, seg_lines, point_lines, legend)
        self._apply_range_bands()
        ax.set_ylim(*_EMPTY_YLIM)
        self._apply_glucose_margins()

    def _make_legend(self, figure: Figure, ax: Axes, fg: str, seg_lines, expected_line):
        """The single-row legend under the glucose axes. The "Expected (model)" entry
        is left out while there is no model to show (a CSV sensor)."""
        handles = list(seg_lines.values())
        if self._show_expected:
            handles.append(expected_line)
        # ncol must equal the number of entries so the legend stays a single row — the
        # reserved bottom margin (_GLUCOSE_BOTTOM_IN) only has room for one row;
        # wrapping to two pushes the second row past the figure's bottom edge and it
        # gets clipped by the canvas.
        legend = ax.legend(
            handles=handles,
            loc="lower center",
            bbox_to_anchor=(0.5, 0.005),
            bbox_transform=figure.transFigure,
            ncol=len(handles),
            fontsize=8,
            frameon=False,
            borderaxespad=0.1,
            borderpad=0.2,
        )
        for text in legend.get_texts():
            text.set_color(fg)
        return legend

    def set_expected_visible(self, show: bool) -> None:
        """Show or hide the expected-model line and its legend entry (a CSV sensor
        runs no model, so there is nothing to label)."""
        if show == self._show_expected:
            return
        self._show_expected = show
        g = self._g
        g.expected_line.set_visible(show)
        g.legend.remove()
        g.legend = self._make_legend(g.figure, g.ax, g.fg, g.seg_lines, g.expected_line)
        self.canvas.draw_idle()

    def _apply_glucose_margins(self) -> None:
        """Set the glucose axes' margins from inches (see _GLUCOSE_TOP_IN)."""
        figure = self._g.figure
        height_in = max(figure.get_figheight(), 0.5)
        bottom_in = _GLUCOSE_BOTTOM_LABELLED_IN if self._time_label else _GLUCOSE_BOTTOM_IN
        bottom = min(bottom_in / height_in, 0.45)
        top = max(1.0 - _GLUCOSE_TOP_IN / height_in, bottom + 0.2)
        figure.subplots_adjust(bottom=bottom, top=top, **_side_margins(figure))

    def _build_food_ex(self) -> None:
        """Food/exercise figure: carbs rate (left axis) and exercise % (right axis)."""
        palette = QApplication.instance().palette()
        bg = palette.color(QPalette.ColorRole.Window).name()
        fg = palette.color(QPalette.ColorRole.WindowText).name()
        carbs_color = palette.color(QPalette.ColorRole.Highlight).name()
        exercise_color = "#e0813f"  # fixed accent, distinct from the theme highlight color

        figure = Figure(facecolor=bg)
        figure.subplots_adjust(**_foodex_margins(figure))
        self.fe_canvas = FigureCanvas(figure)
        self.fe_canvas.setMinimumHeight(_FOODEX_MIN_HEIGHT_PX)
        self.fe_canvas.mpl_connect(
            "resize_event", lambda _e: figure.subplots_adjust(**_foodex_margins(figure))
        )
        ax = figure.add_subplot(111, facecolor=bg)
        ax.set_title("Food / Exercise", color=fg)
        ax.set_xlabel("Time (s)", color=fg)
        ax.set_ylabel("Carbs (g/min)", color=fg)
        ax.tick_params(colors=fg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        ax.grid(True, color=fg, alpha=0.15)
        (carbs_line,) = ax.plot([], [], lw=1.5, color=carbs_color, label="Carbs (g/min)")

        ax2 = ax.twinx()
        ax2.set_ylabel("Exercise (%)", color=fg)
        ax2.tick_params(colors=fg)
        (exercise_line,) = ax2.plot(
            [], [], lw=1.5, color=exercise_color, linestyle=":", label="Exercise (%)"
        )

        lines = [carbs_line, exercise_line]
        legend = ax.legend(
            lines,
            [line.get_label() for line in lines],
            loc="lower center",
            bbox_to_anchor=(0.5, 0.005),
            bbox_transform=figure.transFigure,
            ncol=2,
            fontsize=8,
            frameon=False,
            borderaxespad=0.1,
            borderpad=0.2,
        )
        for text in legend.get_texts():
            text.set_color(fg)
        self._fe = _FoodExPlot(figure, ax, ax2, carbs_line, exercise_line)

    def rebuild_for_theme(self, splitter: QSplitter) -> None:
        """Recreate both canvases in place (they read the palette only at build
        time), preserving the current data and splitter sizes."""
        sizes = splitter.sizes()
        self._build_glucose()
        self._build_food_ex()
        old_g = splitter.replaceWidget(0, self.canvas)
        old_fe = splitter.replaceWidget(1, self.fe_canvas)
        for old in (old_g, old_fe):
            if old is not None:
                old.deleteLater()
        splitter.setSizes(sizes)
        self.redraw_glucose()
        self.redraw_food_ex()

    # ------------------------------------------------------------------
    # State the owner drives
    # ------------------------------------------------------------------

    def elapsed_seconds(self, timestamp_str: str) -> float:
        """ISO timestamp -> simulated seconds on the shared timeline."""
        return self.clock.elapsed_seconds(timestamp_str)

    def clear(self) -> None:
        """Drop every plot buffer (the caller re-anchors the clock, then redraws)."""
        self.buf = _Buffers()

    def append_received(self, t: float, glucose: float) -> None:
        """One received (board) glucose point."""
        self.buf.graph_x.append(t)
        self.buf.graph_y.append(glucose)

    def append_expected(self, t: float, glucose: float) -> None:
        """One point of the local model's expected line."""
        self.buf.expected_x.append(t)
        self.buf.expected_y.append(glucose)

    def append_food_ex(self, t: float, carbs_rate: float, exercise_pct: float) -> None:
        """One food/exercise sample."""
        self.buf.food_ex_x.append(t)
        self.buf.food_ex_carbs_y.append(carbs_rate)
        self.buf.food_ex_exercise_y.append(exercise_pct)

    def clear_expected(self) -> None:
        """Forget the expected line (the sensor turned out to be replaying a CSV)."""
        self.buf.expected_x.clear()
        self.buf.expected_y.clear()

    def set_show_points(self, show: bool) -> None:
        """Show (or hide) each received sample as a dot on the glucose line."""
        self._show_points = show
        for line in self._g.point_lines.values():
            line.set_visible(show)
        self.canvas.draw_idle()

    def set_time_label(self, show: bool) -> None:
        """Label the glucose graph's x-axis "Time (s)" itself — needed when the
        food/exercise graph below, which normally carries the label, is hidden."""
        if show == self._time_label:
            return
        self._time_label = show
        g = self._g
        g.ax.set_xlabel("Time (s)" if show else "", color=g.fg)
        self._apply_glucose_margins()
        self.canvas.draw_idle()

    def set_thresholds(self, thresholds: dict) -> None:
        self.thresholds = thresholds
        self._apply_range_bands()
        self.redraw_glucose()

    def set_view_window(self, seconds: float) -> None:
        self.view_window_s = seconds
        self.redraw_glucose()
        self.redraw_food_ex()

    def set_glucose_title(self, text: str) -> None:
        self._g.ax.set_title(text, color=self._g.fg, fontweight="bold", fontsize=11)
        self.canvas.draw_idle()

    def add_pisa_span(self, t_start_s: float, t_end_s: float) -> None:
        """Shade a PISA interval on this graph."""
        self.buf.pisa_spans.append((t_start_s, t_end_s))

    # ------------------------------------------------------------------
    # Range bands / categories
    # ------------------------------------------------------------------

    def _apply_range_bands(self) -> None:
        """(Re)draw the 5 horizontal range bands from the current thresholds.

        axhspan patches don't feed the data limits, so the y-axis still
        autoscales to the glucose lines alone.
        """
        for patch in self._g.range_bands:
            patch.remove()
        t = self.thresholds
        edges = [0.0, t["tbr2_below"], t["tbr1_below"], t["tar1_above"], t["tar2_above"], 600.0]
        self._g.range_bands = [
            self._g.ax.axhspan(lo, hi, color=color, alpha=0.09, zorder=0)
            for lo, hi, color in zip(edges, edges[1:], _BAND_COLORS)
        ]

    def _category(self, value: float) -> str:
        """'r' out of range (low/high), 'y' borderline, 'g' in target — vs current thresholds."""
        return _CATEGORY_OF_LEVEL[alerts.alert_for(value, self.thresholds).level]

    def _colored_segments(self) -> dict[str, tuple[list[float], list[float]]]:
        """The received trace cut into runs of one range category.

        A segment that crosses a threshold is split AT the crossing, by linear
        interpolation, so the line changes colour exactly where it passes the
        limit — not at the next sample. Each run is drawn on its category's line;
        runs are separated by NaN so they never join up.
        """
        t = self.thresholds
        edges = sorted((t["tbr2_below"], t["tbr1_below"], t["tar1_above"], t["tar2_above"]))
        xs, ys = self.buf.graph_x, self.buf.graph_y
        nan = float("nan")
        out: dict[str, tuple[list[float], list[float]]] = {c: ([], []) for c in _LINE_COLORS}
        tail: dict[str, tuple[float, float] | None] = dict.fromkeys(_LINE_COLORS)

        def add(cat: str, a: tuple[float, float], b: tuple[float, float]) -> None:
            cx, cy = out[cat]
            if tail[cat] != a:  # not continuing the previous piece: start a new run
                if cx:
                    cx.append(nan)
                    cy.append(nan)
                cx.append(a[0])
                cy.append(a[1])
            cx.append(b[0])
            cy.append(b[1])
            tail[cat] = b

        for i in range(len(xs) - 1):
            x0, y0, x1, y1 = xs[i], ys[i], xs[i + 1], ys[i + 1]
            if y0 != y0 or y1 != y1:  # a NaN gap in the data
                continue
            cuts = [(x0, y0)]
            if y0 != y1:
                lo, hi = (y0, y1) if y0 < y1 else (y1, y0)
                crossings = [
                    (x0 + (e - y0) * (x1 - x0) / (y1 - y0), e) for e in edges if lo < e < hi
                ]
                cuts += sorted(crossings, key=lambda c: c[0])
            cuts.append((x1, y1))
            for a, b in itertools.pairwise(cuts):
                add(self._category((a[1] + b[1]) / 2.0), a, b)
        return out

    def _recolor_main_trace(self) -> None:
        """Draw the received trace in its range colours, and its dots if enabled."""
        runs = self._colored_segments()
        for cat, line in self._g.seg_lines.items():
            line.set_data(*runs[cat])
        xs, ys = self.buf.graph_x, self.buf.graph_y
        dots: dict[str, tuple[list[float], list[float]]] = {c: ([], []) for c in _LINE_COLORS}
        for x, y in zip(xs, ys):
            if y == y:
                dots[self._category(y)][0].append(x)
                dots[self._category(y)][1].append(y)
        for cat, line in self._g.point_lines.items():
            line.set_data(*dots[cat])

    # ------------------------------------------------------------------
    # Axis sync / limits / shading
    # ------------------------------------------------------------------

    def _sync_time_axis(self) -> None:
        """Give both stacked graphs the same x-range so points at the same time line up.

        Honours the rolling view window (view_window_s): when set, only the last
        N seconds are shown, while the full data arrays are kept so switching to
        "Entire run" reveals everything again.
        """
        b = self.buf
        xs = b.graph_x + b.expected_x + b.food_ex_x
        if not xs:
            self.visible_xlim = None
            return
        lo, hi = min(xs), max(xs)
        if hi <= lo:
            hi = lo + 1.0
        if self.view_window_s and (hi - lo) > self.view_window_s:
            lo = hi - self.view_window_s
        self.visible_xlim = (lo, hi)
        self._g.ax.set_xlim(lo, hi)
        self._fe.ax.set_xlim(lo, hi)

    def in_view(self, xs: list[float], ys: list[float]) -> list[float]:
        """Return the ys whose x is inside the currently visible window (NaNs dropped)."""
        lo = self.visible_xlim[0] if self.visible_xlim else float("-inf")
        return [y for x, y in zip(xs, ys) if x >= lo and y == y]

    def _draw_pisa_spans(self) -> None:
        """(Re)shade the PISA intervals on the glucose graph."""
        for patch in self.pisa_patches:
            try:
                patch.remove()
            except (ValueError, AttributeError):
                pass
        self.pisa_patches = [
            self._g.ax.axvspan(a, b, color="#8e44ad", alpha=0.10, zorder=0)
            for a, b in self.buf.pisa_spans
        ]

    def _fit_glucose_ylim(self) -> None:
        """Set the glucose y-axis to the data's own min/max plus a small margin.

        Explicit instead of autoscale so the range bands (which extend well past
        any real reading) can't stretch the axis up to 600.
        """
        b = self.buf
        ys = self.in_view(b.graph_x, b.graph_y) + self.in_view(b.expected_x, b.expected_y)
        if not ys:
            self._g.ax.set_ylim(*_EMPTY_YLIM)
            return
        lo, hi = min(ys), max(ys)
        pad = max(10.0, (hi - lo) * 0.10)
        self._g.ax.set_ylim(max(0.0, lo - pad), hi + pad)

    # ------------------------------------------------------------------
    # Redraw
    # ------------------------------------------------------------------

    def redraw_glucose(self) -> None:
        """Push updated x/y data to both line artists and request a canvas refresh."""
        b = self.buf
        self._recolor_main_trace()
        self._g.expected_line.set_data(b.expected_x, b.expected_y)
        self._sync_time_axis()  # sets self.visible_xlim, used by the helpers below
        self._draw_pisa_spans()
        self._fit_glucose_ylim()
        if self._on_redraw is not None:
            self._on_redraw()
        self.canvas.draw_idle()
        self.fe_canvas.draw_idle()

    def redraw_food_ex(self) -> None:
        """Push updated x/y data to the food/exercise line artists and refresh."""
        b = self.buf
        self._fe.ax.set_title(self._fe_title(), color=self._g.fg)
        self._fe.carbs_line.set_data(b.food_ex_x, b.food_ex_carbs_y)
        self._fe.exercise_line.set_data(b.food_ex_x, b.food_ex_exercise_y)
        self._sync_time_axis()
        cvis = self.in_view(b.food_ex_x, b.food_ex_carbs_y)
        evis = self.in_view(b.food_ex_x, b.food_ex_exercise_y)
        self._fe.ax.set_ylim(0.0, max(1.0, (max(cvis) if cvis else 0.0) * 1.15))
        self._fe.ax2.set_ylim(0.0, max(1.0, (max(evis) if evis else 0.0) * 1.15))
        self.fe_canvas.draw_idle()
        self.canvas.draw_idle()
