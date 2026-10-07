"""A small 24 h graph of one recurring daily schedule (meals or exercise), minute by minute.

One matplotlib axes, themed from the application palette like the glucose graph, with the day
along the bottom in hours. The page that owns it calls :meth:`ScheduleGraph.set_values`
whenever the schedule changes.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication

_MIN_HEIGHT_PX = 170
_HOURS = [m / 60.0 for m in range(1441)]


class ScheduleGraph(FigureCanvas):
    """A filled step curve over one day. ``values`` holds the 1440 per-minute values drawn."""

    def __init__(self, title: str, ylabel: str, color: str) -> None:
        palette = QApplication.palette()
        background = palette.color(QPalette.ColorRole.Window).name()
        foreground = palette.color(QPalette.ColorRole.WindowText).name()
        self._color = color
        figure = Figure(facecolor=background)
        super().__init__(figure)
        self.setMinimumHeight(_MIN_HEIGHT_PX)
        self.ax = figure.add_subplot(111, facecolor=background)
        self.ax.set_title(title, color=foreground, fontsize=10)
        self.ax.set_xlabel("Time of day (h)", color=foreground)
        self.ax.set_ylabel(ylabel, color=foreground)
        self.ax.set_xlim(0, 24)
        self.ax.set_xticks(range(0, 25, 3))
        self.ax.tick_params(colors=foreground)
        for spine in self.ax.spines.values():
            spine.set_color(foreground)
        self.ax.grid(True, color=foreground, alpha=0.15)
        figure.subplots_adjust(left=0.1, right=0.98, top=0.88, bottom=0.25)
        self.values: list[float] = [0.0] * 1440
        self._fill = None
        self.set_values(self.values)

    def set_values(self, values: list[float]) -> None:
        """Draw *values* (one per minute of the day) as a filled step curve."""
        self.values = list(values)
        if self._fill is not None:
            self._fill.remove()
        # one extra point so the last minute has a width too
        steps = [*self.values, self.values[-1] if self.values else 0.0]
        self._fill = self.ax.fill_between(
            _HOURS[: len(steps)], steps, step="post", color=self._color, alpha=0.7
        )
        top = max(self.values, default=0.0)
        self.ax.set_ylim(0, top * 1.2 if top > 0 else 1.0)
        self.draw_idle()
