"""The 24 h preview of a user: what a day of this user looks like, before it is sent anywhere.

Model mode runs the same noise-free :class:`~models.engine.ModelStepper` as the expected line over
the user's food, exercise and glucose model (:func:`models.user_sim.preview_24h`); CSV mode shows
the recorded window. **No sensor noise** is applied either way — it is the underlying glucose, not
what a sensor would report. Meals are marked and exercise bouts shaded so the curve can be read
against what drives it. It previews whatever is on the screen, saved or not.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from gui.widgets import fit_to_screen, wrapped_label
from models import user_sim
from models.types import User


class PreviewWindow(QWidget):
    """A window with the user's 24 h glucose curve."""

    def __init__(self, user: User, thresholds: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(f"Preview — {user.name}")
        fit_to_screen(self, 760, 420)

        self.minutes, self.glucose = user_sim.preview_24h(user)
        self.meal_marks = 0
        self.exercise_spans = 0
        self.threshold_lines = 0

        palette = QApplication.palette()
        background = palette.color(QPalette.ColorRole.Window).name()
        self._foreground = palette.color(QPalette.ColorRole.WindowText).name()
        figure = Figure(facecolor=background)
        self.canvas = FigureCanvas(figure)
        self.ax = figure.add_subplot(111, facecolor=background)
        self._draw(user, thresholds)
        figure.subplots_adjust(left=0.08, right=0.98, top=0.9, bottom=0.14)

        layout = QVBoxLayout(self)
        layout.addWidget(self.canvas, 1)
        self.note = wrapped_label(self._note_text(user), muted=True)
        layout.addWidget(self.note)

    # ------------------------------------------------------------------

    def _note_text(self, user: User) -> str:
        if user.mode == "csv":
            if not self.glucose:
                return "No CSV window to preview — choose one on the CSV page."
            return "The recorded window, as it will be replayed. No sensor noise is added."
        return (
            "Simulated from midnight with this user's meals, exercise and glucose model. "
            "No sensor noise is applied: this is the underlying glucose, not a sensor's reading."
        )

    def _draw(self, user: User, thresholds: dict) -> None:
        ax, fg = self.ax, self._foreground
        ax.set_title(f"{user.name} — 24 h", color=fg)
        ax.set_xlabel("Time of day (h)", color=fg)
        ax.set_ylabel("Glucose (mg/dL)", color=fg)
        ax.set_xlim(0, 24)
        ax.set_xticks(range(0, 25, 3))
        ax.tick_params(colors=fg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        ax.grid(True, color=fg, alpha=0.15)
        if self.glucose:
            ax.plot([m / 60.0 for m in self.minutes], self.glucose, color="#3f8fd0", lw=1.6)
        for key in ("tbr1_below", "tar1_above"):
            if key in thresholds:
                ax.axhline(thresholds[key], color="#c0504d", ls="--", lw=0.9, alpha=0.7)
                self.threshold_lines += 1
        if user.mode == "model":
            for meal in user.food_events:
                ax.axvline(meal.time_of_day_min / 60.0, color="#2e9e5b", lw=1.0, alpha=0.8)
                self.meal_marks += 1
            for bout in user.exercise_events:
                start = bout.time_of_day_min / 60.0
                ax.axvspan(start, start + bout.duration_min / 60.0, color="#e0813f", alpha=0.2)
                self.exercise_spans += 1
