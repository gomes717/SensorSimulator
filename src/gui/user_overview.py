"""The profile screen's overview of one user's day: the 24 h glucose preview with the food and
exercise schedules under it, on one shared time axis.

Model mode runs the same noise-free :class:`~models.engine.ModelStepper` as the expected line over
the user's food, exercise and glucose model (:func:`models.user_sim.preview_24h`); CSV mode shows
the recorded window. **No sensor noise** is applied either way — it is the underlying glucose, not
what a sensor would report. The range bands are the ones the live graph uses, so the curve reads
against the same regions. Meals are marked and exercise bouts shaded on the glucose plot; the two
plots under it draw the schedules themselves.

:meth:`UserOverview.refresh` redraws from the user it was given, so the screen calls it after every
edit and the graphs follow the tables as they change (one day of the model takes about 10 ms).
"""

from __future__ import annotations

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from gui.range_bands import draw_range_bands
from gui.widgets import wrapped_label
from models import app_settings, user_schedule, user_sim
from models.types import User

_GLUCOSE_COLOR = "#3f8fd0"
_FOOD_COLOR = "#2e9e5b"
_EXERCISE_COLOR = "#e0813f"
_HOURS = [m / 60.0 for m in range(1441)]
_ROW_RATIOS = (2.6, 1.0, 1.0)
_HIDDEN_RATIO = 0.001  # a collapsed row (the schedules of a CSV user)
_MIN_HEIGHT_PX = 340


class UserOverview(QWidget):
    """Glucose preview, meals and exercise of one user, redrawn by :meth:`refresh`."""

    def __init__(self, user: User, thresholds: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self._user = user
        self._thresholds = thresholds if thresholds is not None else app_settings.load()
        self._preview: tuple[list[float], list[float]] = ([], [])  # (minutes, mg/dL)
        self.food_values: list[float] = []
        self.exercise_values: list[float] = []
        self.meal_marks = 0
        self.exercise_spans = 0
        self.band_count = 0

        palette = QApplication.palette()
        background = palette.color(QPalette.ColorRole.Window).name()
        self._foreground = palette.color(QPalette.ColorRole.WindowText).name()
        figure = Figure(facecolor=background, layout="constrained")
        engine = figure.get_layout_engine()
        if engine is not None:
            engine.set(w_pad=0.03, h_pad=0.03)
        self.canvas = FigureCanvas(figure)
        self.canvas.setMinimumHeight(_MIN_HEIGHT_PX)
        self._grid = figure.add_gridspec(3, 1, height_ratios=_ROW_RATIOS)
        self.glucose_ax = figure.add_subplot(self._grid[0], facecolor=background)
        self.food_ax = figure.add_subplot(
            self._grid[1], facecolor=background, sharex=self.glucose_ax
        )
        self.exercise_ax = figure.add_subplot(
            self._grid[2], facecolor=background, sharex=self.glucose_ax
        )
        self.note = wrapped_label("", muted=True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.note)
        self.refresh()

    @property
    def minutes(self) -> list[float]:
        """The preview's time axis, in minutes."""
        return self._preview[0]

    @property
    def glucose(self) -> list[float]:
        """The preview's glucose, in mg/dL."""
        return self._preview[1]

    # ------------------------------------------------------------------

    def set_thresholds(self, thresholds: dict) -> None:
        """New range thresholds: redraw the bands."""
        self._thresholds = thresholds
        self.refresh()

    def refresh(self) -> None:
        """Recompute the preview and redraw all three plots from the user as it is now."""
        user = self._user
        self._preview = user_sim.preview_24h(user)
        is_csv = user.mode == "csv"
        self.food_values = [] if is_csv else user_schedule.food_rate_series(user.food_events)
        self.exercise_values = [] if is_csv else user_schedule.exercise_series(user.exercise_events)
        self._grid.set_height_ratios(
            _ROW_RATIOS if not is_csv else (_ROW_RATIOS[0], _HIDDEN_RATIO, _HIDDEN_RATIO)
        )
        for ax in (self.food_ax, self.exercise_ax):
            ax.set_visible(not is_csv)
        self._draw_glucose(user)
        self._draw_schedule(self.food_ax, self.food_values, "Carbs g/min", _FOOD_COLOR)
        self._draw_schedule(self.exercise_ax, self.exercise_values, "Exercise %", _EXERCISE_COLOR)
        self.exercise_ax.set_xlabel("Time of day (h)", color=self._foreground)
        self.glucose_ax.set_xlim(0, 24)
        self.glucose_ax.set_xticks(range(0, 25, 3))
        self.note.setText(self._note_text(user))
        self.canvas.draw_idle()

    def _note_text(self, user: User) -> str:
        if user.mode == "csv":
            if not self.glucose:
                return "No CSV window to preview — choose one on the CSV page."
            return "The recorded window, as it will be replayed. No sensor noise is added."
        return (
            "Simulated from midnight with this user's meals, exercise and glucose model. "
            "No sensor noise is applied: this is the underlying glucose, not a sensor's reading."
        )

    # ------------------------------------------------------------------

    def _style(self, ax, ylabel: str) -> None:
        fg = self._foreground
        # Small, so the label of a short schedule plot fits its height instead of overlapping.
        ax.set_ylabel(ylabel, color=fg, fontsize=8)
        ax.tick_params(colors=fg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        ax.grid(True, color=fg, alpha=0.15)

    def _draw_glucose(self, user: User) -> None:
        ax = self.glucose_ax
        ax.clear()
        self._style(ax, "Glucose (mg/dL)")
        ax.set_title(f"{user.name} — 24 h", color=self._foreground, fontsize=10)
        ax.tick_params(labelbottom=False)
        self.band_count = len(draw_range_bands(ax, self._thresholds))
        if self.glucose:
            ax.plot([m / 60.0 for m in self.minutes], self.glucose, color=_GLUCOSE_COLOR, lw=1.6)
        self.meal_marks = 0
        self.exercise_spans = 0
        if user.mode == "model":
            for meal in user.food_events:
                ax.axvline(meal.time_of_day_min / 60.0, color=_FOOD_COLOR, lw=1.0, alpha=0.8)
                self.meal_marks += 1
            for bout in user.exercise_events:
                start = bout.time_of_day_min / 60.0
                ax.axvspan(
                    start, start + bout.duration_min / 60.0, color=_EXERCISE_COLOR, alpha=0.2
                )
                self.exercise_spans += 1
        if self.glucose:
            low, high = min(self.glucose), max(self.glucose)
            pad = max(10.0, (high - low) * 0.1)
            ax.set_ylim(low - pad, high + pad)

    def _draw_schedule(self, ax, values: list[float], ylabel: str, color: str) -> None:
        ax.clear()
        self._style(ax, ylabel)
        if not values:
            return
        steps = [*values, values[-1]]  # one extra point so the last minute has a width too
        ax.fill_between(_HOURS[: len(steps)], steps, step="post", color=color, alpha=0.7)
        top = max(values)
        ax.set_ylim(0, top * 1.2 if top > 0 else 1.0)
        if ax is not self.exercise_ax:
            ax.tick_params(labelbottom=False)
