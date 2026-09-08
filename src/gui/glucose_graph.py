"""The two stacked matplotlib panels — glucose (received + expected + range
bands + PISA shading) and food/exercise — as one deep module.

Pulled out of :class:`MainWindow` (issue 18): ~380 lines of figure
construction, theming, range-band math, per-category recolouring, rolling-window
time-axis sync, y-limit fitting and redraw bookkeeping lived on the god object,
tangled with its BLE + engine state. Here they sit behind a small interface.

The owner (``MainWindow``) still holds the *per-user* history dict and decides
which user is selected; it points this panel's live buffers at the selected
user's lists via :meth:`bind_buffers` (aliasing, not copying — appends made by
the owner land straight in the artists on the next :meth:`redraw_glucose`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.axes import Axes
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QSplitter

# red = TBR2/TAR2 (out of range), yellow = TBR1/TAR1 (borderline), green = TIR
_RED = "#d32f2f"
_YELLOW = "#f4b400"
_GREEN = "#2e7d32"
_LINE_COLORS = {"r": _RED, "y": _YELLOW, "g": _GREEN}
# Clinical range band colors (translucent), same 5-band order: TBR2, TBR1, TIR, TAR1, TAR2.
_BAND_COLORS = (_RED, _YELLOW, _GREEN, _YELLOW, _RED)
# Left/right are shared so the two stacked plot boxes line up on the time
# axis; top/bottom differ — the glucose graph only reserves room for its
# legend, the food/exercise graph also carries the "Time (s)" label.
_GLUCOSE_MARGINS = dict(left=0.09, right=0.91, top=0.90, bottom=0.18)
_FOODEX_MARGINS = dict(left=0.09, right=0.91, top=0.86, bottom=0.32)
# y-axis when the glucose graph has no data yet (mg/dL)
_EMPTY_YLIM = (40.0, 200.0)


@dataclass
class _Buffers:
    """The live plot series. Reassigned wholesale by bind_buffers()/reset() to
    alias the selected user's history lists; the owner appends to them."""

    graph_x: list[float] = field(default_factory=list)
    graph_y: list[float] = field(default_factory=list)
    expected_x: list[float] = field(default_factory=list)
    expected_y: list[float] = field(default_factory=list)
    food_ex_x: list[float] = field(default_factory=list)
    food_ex_carbs_y: list[float] = field(default_factory=list)
    food_ex_exercise_y: list[float] = field(default_factory=list)


@dataclass
class _GlucosePlot:
    """Artists for the glucose figure (the canvas is kept on GlucoseGraph)."""

    figure: Figure
    ax: Axes
    fg: str
    base_line: Line2D  # faint continuous base under the colored category segments
    expected_line: Line2D
    mean_line: Line2D
    seg_lines: dict[str, Line2D]
    range_bands: list = field(default_factory=list)


@dataclass
class _FoodExPlot:
    """Artists for the food/exercise figure."""

    figure: Figure
    ax: Axes
    ax2: Axes
    carbs_line: Line2D
    exercise_line: Line2D


class GlucoseGraph:
    """Owns the glucose + food/exercise canvases and every draw decision for them."""

    def __init__(
        self,
        thresholds: dict,
        view_window_s: float,
        graph_t0: datetime,
        *,
        on_redraw: Callable[[], None] | None = None,
        fe_title: Callable[[], str] | None = None,
    ) -> None:
        self.thresholds = thresholds
        self.view_window_s = view_window_s
        self.graph_t0 = graph_t0
        self._on_redraw = on_redraw
        self._fe_title = fe_title or (lambda: "Food / Exercise")

        self.buf = _Buffers()
        # PISA-shaded intervals (t_start_s, t_end_s) in graph time + their patches.
        self.pisa_spans: list[tuple[float, float]] = []
        self.pisa_patches: list = []
        self.visible_xlim: tuple[float, float] | None = None

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
        category, a dashed "expected" line, a mean line and the 5 range bands.

        Colors come from the live *application* palette (not a widget's) so a
        rebuild right after a theme switch sees the new colors immediately.
        """
        palette = QApplication.instance().palette()
        bg = palette.color(QPalette.ColorRole.Window).name()
        fg = palette.color(QPalette.ColorRole.WindowText).name()
        accent = palette.color(QPalette.ColorRole.Highlight).name()

        figure = Figure(facecolor=bg)
        figure.subplots_adjust(**_GLUCOSE_MARGINS)
        self.canvas = FigureCanvas(figure)
        ax = figure.add_subplot(111, facecolor=bg)
        ax.set_title("Select a user", color=fg)
        ax.set_ylabel("Glucose (mg/dL)", color=fg)
        ax.tick_params(colors=fg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        ax.grid(True, color=fg, alpha=0.15)
        mean_line = ax.axhline(0.0, color=fg, lw=1.0, linestyle="-.", alpha=0.0, label="Mean")
        (base_line,) = ax.plot([], [], lw=0.8, color=fg, alpha=0.35)  # faint base for the segs
        seg_labels = {"g": "In range", "y": "Borderline", "r": "Low / High"}
        seg_lines = {
            cat: ax.plot(
                [], [], lw=1.7, color=color, solid_capstyle="round", label=seg_labels[cat]
            )[0]
            for cat, color in _LINE_COLORS.items()
        }
        (expected_line,) = ax.plot(
            [], [], lw=1.5, color=accent, linestyle="--", alpha=0.7, label="Expected (model)"
        )
        legend = ax.legend(
            loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=3, fontsize=8, frameon=False
        )
        for text in legend.get_texts():
            text.set_color(fg)
        self._g = _GlucosePlot(figure, ax, fg, base_line, expected_line, mean_line, seg_lines)
        self._apply_range_bands()
        ax.set_ylim(*_EMPTY_YLIM)

    def _build_food_ex(self) -> None:
        """Food/exercise figure: carbs rate (left axis) and exercise % (right axis)."""
        palette = QApplication.instance().palette()
        bg = palette.color(QPalette.ColorRole.Window).name()
        fg = palette.color(QPalette.ColorRole.WindowText).name()
        carbs_color = palette.color(QPalette.ColorRole.Highlight).name()
        exercise_color = "#e0813f"  # fixed accent, distinct from the theme highlight color

        figure = Figure(facecolor=bg)
        figure.subplots_adjust(**_FOODEX_MARGINS)
        self.fe_canvas = FigureCanvas(figure)
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
            loc="upper center",
            bbox_to_anchor=(0.5, -0.34),
            ncol=2,
            fontsize=8,
            frameon=False,
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
        """ISO timestamp (BLE message or engine tick) -> seconds since graph_t0,
        the shared x-axis unit for both graphs."""
        return (datetime.fromisoformat(timestamp_str) - self.graph_t0).total_seconds()

    def reset(self, now: datetime) -> None:
        """Re-anchor the shared timeline at t=0 and drop every plot buffer.

        The owner calls this on every genuine restart (Start/Stop, switching
        person/sensor/mode, saving a profile). A single shared reset point is
        what keeps the received and expected lines on the same time origin.
        The owner then rebinds the live buffers and redraws.
        """
        self.graph_t0 = now
        self.buf = _Buffers()
        self.pisa_spans = []

    def bind_buffers(
        self,
        gx: list[float],
        gy: list[float],
        fx: list[float],
        fc: list[float],
        fe: list[float],
        ex_gx: list[float] | None = None,
        ex_gy: list[float] | None = None,
    ) -> None:
        """Alias the live plot buffers onto the selected user's history lists."""
        self.buf.graph_x, self.buf.graph_y = gx, gy
        self.buf.food_ex_x, self.buf.food_ex_carbs_y, self.buf.food_ex_exercise_y = fx, fc, fe
        if ex_gx is not None and ex_gy is not None:
            self.buf.expected_x, self.buf.expected_y = ex_gx, ex_gy

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
        self.pisa_spans.append((t_start_s, t_end_s))

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
        t = self.thresholds
        if value < t["tbr2_below"] or value > t["tar2_above"]:
            return "r"
        if value < t["tbr1_below"] or value > t["tar1_above"]:
            return "y"
        return "g"

    def _recolor_main_trace(self) -> None:
        """Split the Received series into red/yellow/green overlays by range category."""
        xs, ys = self.buf.graph_x, self.buf.graph_y
        n = len(ys)
        nan = float("nan")
        arrs = {"r": [nan] * n, "y": [nan] * n, "g": [nan] * n}
        if n:
            cats = [self._category(v) for v in ys]
            for i, v in enumerate(ys):
                here = cats[i]
                arrs[here][i] = v
                if i > 0 and cats[i - 1] != here:
                    arrs[cats[i - 1]][i] = v
                if i < n - 1 and cats[i + 1] != here:
                    arrs[cats[i + 1]][i] = v
        for cat, line in self._g.seg_lines.items():
            line.set_data(xs, arrs[cat])

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
            for a, b in self.pisa_spans
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
        self._g.base_line.set_data(b.graph_x, b.graph_y)
        self._recolor_main_trace()
        self._g.expected_line.set_data(b.expected_x, b.expected_y)
        self._sync_time_axis()  # sets self.visible_xlim, used by the helpers below
        self._draw_pisa_spans()
        self._fit_glucose_ylim()
        # Mean of the *received* (board) series only — never the expected line.
        # A freshly-selected sensor with an empty history would otherwise show
        # the mean of the Python model, which reads as "the board is tracking
        # the model".
        series = self.in_view(b.graph_x, b.graph_y)
        if series:
            mean = sum(series) / len(series)
            self._g.mean_line.set_ydata([mean, mean])
            self._g.mean_line.set_alpha(0.6)
        else:
            self._g.mean_line.set_alpha(0.0)
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
