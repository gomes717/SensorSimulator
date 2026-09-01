"""View configuration window — appearance settings. First control: light/dark theme."""
from __future__ import annotations

from typing import Callable

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


class ViewConfigWindow(QWidget):
    """Appearance settings. *on_theme_changed* is called after the palette is applied
    so the caller can rebuild anything that cached the old colors (the graphs)."""

    def __init__(self, on_theme_changed: Callable[[], None], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("View")
        self.resize(320, 160)
        self._on_theme_changed = on_theme_changed

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

        layout.addWidget(group)
        layout.addStretch(1)

    def _on_theme_selected(self, _index: int) -> None:
        mode = self._theme_combo.currentData()
        app_settings.save_theme(mode)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode)
        self._on_theme_changed()
