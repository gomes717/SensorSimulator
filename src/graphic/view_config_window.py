"""View configuration window — appearance settings. First control: light/dark theme."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QVBoxLayout,
    QWidget,
)

from graphic.theme import apply_theme
from models import app_settings

_THEME_LABELS = [("system", "System default"), ("light", "Light"), ("dark", "Dark")]
# (seconds, label); 0 = show the whole run
_WINDOW_CHOICES = [
    (3600, "Last 1 hour"),
    (21600, "Last 6 hours"),
    (86400, "Last 24 hours"),
    (0, "Entire run"),
]


class ViewConfigWindow(QWidget):
    """Appearance + graph-view settings.

    *on_theme_changed* is called after the palette is applied so the caller can
    rebuild anything that cached the old colors (the graphs). *on_view_changed*
    is called after the graph time-window preference changes.
    """

    def __init__(
        self,
        on_theme_changed: Callable[[], None],
        on_view_changed: Callable[[], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("View")
        self.resize(320, 200)
        self._on_theme_changed = on_theme_changed
        self._on_view_changed = on_view_changed

        layout = QVBoxLayout(self)
        group = QGroupBox("Appearance")
        form = QFormLayout(group)

        self._theme_combo = QComboBox()
        for value, label in _THEME_LABELS:
            self._theme_combo.addItem(label, value)
        current = app_settings.load_theme()
        idx = next((i for i, (v, _) in enumerate(_THEME_LABELS) if v == current), 0)
        self._theme_combo.setCurrentIndex(idx)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_selected)
        form.addRow("Theme:", self._theme_combo)

        self._window_combo = QComboBox()
        for seconds, label in _WINDOW_CHOICES:
            self._window_combo.addItem(label, seconds)
        cur = int(app_settings.load_pref("view_window_s", 3600))
        widx = next((i for i, (s, _) in enumerate(_WINDOW_CHOICES) if s == cur), 0)
        self._window_combo.setCurrentIndex(widx)
        self._window_combo.currentIndexChanged.connect(self._on_window_selected)
        form.addRow("Graph time window:", self._window_combo)

        layout.addWidget(group)
        layout.addStretch(1)

    def _on_theme_selected(self, _index: int) -> None:
        mode = self._theme_combo.currentData()
        app_settings.save_theme(mode)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode)
        self._on_theme_changed()

    def _on_window_selected(self, _index: int) -> None:
        app_settings.save_pref("view_window_s", int(self._window_combo.currentData()))
        if self._on_view_changed is not None:
            self._on_view_changed()
