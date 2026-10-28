"""The profile screen's overview of one user's day: the 24 h glucose preview, with the range
regions, the meals marked and the exercise bouts shaded.

Model mode runs the same noise-free :class:`~models.engine.ModelStepper` as the expected line over
the user's food, exercise and glucose model (:func:`models.user_sim.preview_24h`); CSV mode shows
the recorded window. **No sensor noise** is applied either way — it is the underlying glucose, not
what a sensor would report. The range bands are the ones the live graph uses, so the curve reads
against the same regions.

:meth:`UserOverview.refresh` redraws from the user it was given, so the screen calls it after every
edit and the graph follows the tables as they change (one day of the model takes about 10 ms).
"""

from __future__ import annotations

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from gui.range_bands import draw_range_bands
from gui.widgets import wrapped_label
from models import app_settings, user_sim
from models.types import User

_GLUCOSE_COLOR = "#3f8fd0"
_FOOD_COLOR = "#2e9e5b"
_EXERCISE_COLOR = "#e0813f"
_MIN_HEIGHT_PX = 300


class UserOverview(QWidget):
    """The 24 h glucose preview of one user, redrawn by :meth:`refresh`."""

    def __init__(self, user: User, thresholds: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self._user = user
        self._thresholds = thresholds if thresholds is not None else app_settings.load()
        self._preview: tuple[list[float], list[float]] = ([], [])  # (minutes, mg/dL)
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
        self.ax = figure.add_subplot(111, facecolor=background)
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
        """Recompute the preview and redraw it from the user as it is now."""
        user = self._user
        self._preview = user_sim.preview_24h(user)
        ax, fg = self.ax, self._foreground
        ax.clear()
        ax.set_title(f"{user.name} — 24 h", color=fg, fontsize=10)
        ax.set_ylabel("Glucose (mg/dL)", color=fg, fontsize=8)
        # A CSV window is not anchored to a time of day: its axis counts from the window's start.
        ax.set_xlabel(
            "Hours into the recorded window" if user.mode == "csv" else "Time of day (h)",
            color=fg,
        )
        ax.tick_params(colors=fg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        ax.grid(True, color=fg, alpha=0.15)
        self.band_count = len(draw_range_bands(ax, self._thresholds))
        if self.glucose:
            ax.plot([m / 60.0 for m in self.minutes], self.glucose, color=_GLUCOSE_COLOR, lw=1.6)
        self._mark_schedule(user)
        if self.glucose:
            low, high = min(self.glucose), max(self.glucose)
            pad = max(10.0, (high - low) * 0.1)
            ax.set_ylim(low - pad, high + pad)
        ax.set_xlim(0, 24)
        ax.set_xticks(range(0, 25, 3))
        self.note.setText(self._note_text(user))
        self.canvas.draw_idle()

    def _mark_schedule(self, user: User) -> None:
        """Mark each meal (a line) and shade each exercise bout, and say which is which."""
        ax = self.ax
        self.meal_marks = 0
        self.exercise_spans = 0
        if user.mode != "model":
            return  # a recording drives a CSV user: there is no schedule to mark
        for meal in user.food_events:
            ax.axvline(meal.time_of_day_min / 60.0, color=_FOOD_COLOR, lw=1.0, alpha=0.8)
            self.meal_marks += 1
        for bout in user.exercise_events:
            start = bout.time_of_day_min / 60.0
            ax.axvspan(start, start + bout.duration_min / 60.0, color=_EXERCISE_COLOR, alpha=0.2)
            self.exercise_spans += 1
        handles = []
        if self.meal_marks:
            handles.append(Line2D([], [], color=_FOOD_COLOR, lw=1.0, label="Meal"))
        if self.exercise_spans:
            handles.append(Patch(color=_EXERCISE_COLOR, alpha=0.3, label="Exercise"))
        if handles:
            legend = ax.legend(handles=handles, loc="upper right", fontsize=8, frameon=False)
            for text in legend.get_texts():
                text.set_color(self._foreground)

    def _note_text(self, user: User) -> str:
        if user.mode == "csv":
            if not self.glucose:
                return "No CSV window to preview — choose one on the CSV page."
            return "The recorded window, as it will be replayed. No sensor noise is added."
        return (
            "Simulated from midnight with this user's meals, exercise and glucose model. "
            "No sensor noise is applied: this is the underlying glucose, not a sensor's reading."
        )
