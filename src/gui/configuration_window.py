"""Configuration window: the mode toggles that used to sit in the main window's bottom
bar, the appearance settings (theme, graph time window — formerly the View window) and
the glucose range thresholds editor. Users (the person and the sensor noise) are edited
from the Users window.

Simulation / BLE state lives on :class:`MainWindow`; this window only hosts the
widgets and talks to the app through a :class:`ConfigController` (a typed signal
surface, issue 18) — it no longer takes ``MainWindow`` or reaches its private members.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.config_controller import ConfigController
from gui.theme import apply_theme
from gui.widgets import NoWheelDoubleSpinBox
from models import app_settings

_THEME_LABELS = [("system", "System default"), ("light", "Light"), ("dark", "Dark")]
# (seconds, label); 0 = show the whole run
_WINDOW_CHOICES = [
    (3600, "Last 1 hour"),
    (21600, "Last 6 hours"),
    (86400, "Last 24 hours"),
    (0, "Entire run"),
]

_THRESHOLD_ROWS = (
    ("tbr2_below", "TBR2 below (mg/dL)"),
    ("tbr1_below", "TBR1 below (mg/dL)"),
    ("tar1_above", "TAR1 above (mg/dL)"),
    ("tar2_above", "TAR2 above (mg/dL)"),
)


class ConfigurationWindow(QWidget):  # pylint: disable=too-many-instance-attributes  # see issue 18
    """Selectors, config buttons, mode toggles and the glucose range thresholds."""

    def __init__(self, controller: ConfigController, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuration")
        self._c = controller
        # Set while the window is repopulating a combo / re-syncing a widget from
        # a controller signal, so the widget's own change handler does not echo
        # the change straight back to the app.
        self._syncing = False

        # Everything sits in a scroll area so a short / low-DPI window never
        # clips the lower groups (issue 16).
        body = QWidget()
        inner = QVBoxLayout(body)
        inner.addWidget(self._build_modes_group())
        inner.addWidget(self._build_appearance_group())
        inner.addWidget(self._build_thresholds_group())
        inner.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # Open at the size the content actually wants. A fixed default (460x540)
        # was narrower and shorter than the body's own size hint, so both
        # scrollbars appeared on a window that had nothing to scroll.
        self._resize_to_content(body, scroll)

        # app -> widget: re-sync widgets after a state change made outside this window.
        controller.speed_display_changed.connect(self._show_speed)
        controller.model_only_display_changed.connect(self._show_model_only)
        controller.controls_locked.connect(self._set_controls_locked)

    def _resize_to_content(self, body: QWidget, scroll: QScrollArea) -> None:
        """Open wide/tall enough for the scroll area's widget, capped to the screen.

        The width budget includes the vertical scrollbar, so a window that is
        tall enough to need one still never needs the horizontal one.
        """
        chrome = 2 * scroll.frameWidth() + scroll.verticalScrollBar().sizeHint().width()
        available = self.screen().availableGeometry()
        # minimumSizeHint, not sizeHint: word-wrapping labels report their whole
        # single-line text as the preferred width, which would open the window
        # far wider than any control needs.
        width = min(body.minimumSizeHint().width() + chrome, available.width())
        # heightForWidth() is -1 when nothing in the layout wraps text ("not applicable"), which is
        # truthy: only a positive answer is a height.
        height = body.heightForWidth(width - chrome)
        if height <= 0:
            height = body.sizeHint().height()
        self.resize(width, min(height + chrome, int(available.height() * 0.9)))

    # ------------------------------------------------------------------
    # Mode toggles
    # ------------------------------------------------------------------

    def _build_modes_group(self) -> QGroupBox:
        group = QGroupBox("Modes")
        box = QVBoxLayout(group)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Simulated time per second:"))
        # Exactly two speeds: real time, or one simulated minute per real second.
        self.speed_combo = QComboBox()
        self.speed_combo.addItem("1 second (real time, x1)", 1.0)
        self.speed_combo.addItem("1 minute (x60)", 60.0)
        self.speed_combo.currentIndexChanged.connect(self._on_speed_chosen)
        speed_row.addWidget(self.speed_combo, 1)
        box.addLayout(speed_row)
        self._show_speed(self._c.speed_mult)

        self.model_only_check = QCheckBox("Model Only (no device)")
        self.model_only_check.toggled.connect(self._on_model_only_toggled)
        box.addWidget(self.model_only_check)
        self.cgms_only_check = QCheckBox("CGMS Only (standard CGM stream only)")
        self.cgms_only_check.toggled.connect(self._on_cgms_only_toggled)
        box.addWidget(self.cgms_only_check)
        return group

    def _on_speed_chosen(self, _index: int) -> None:
        if self._syncing:
            return
        self._drop_custom_speed()
        self._c.speed_change_requested.emit(float(self.speed_combo.currentData()))

    def _drop_custom_speed(self) -> None:
        """Remove the transient "xN" entry once a real choice is made."""
        custom = self.speed_combo.findData("custom")
        if custom >= 0 and self.speed_combo.currentIndex() != custom:
            self.speed_combo.removeItem(custom)

    def _show_speed(self, mult: float) -> None:
        """Show *mult* without emitting a change back. A multiplier that is neither
        choice (a scenario step, an older saved setting) appears as a read-only
        "xN" entry until the user picks one of the two."""
        self._syncing = True
        index = self.speed_combo.findData(float(mult))
        if index < 0:
            custom = self.speed_combo.findData("custom")
            label = f"x{mult:g} (set elsewhere)"
            if custom < 0:
                self.speed_combo.addItem(label, "custom")
            else:
                self.speed_combo.setItemText(custom, label)
            index = self.speed_combo.findData("custom")
        self.speed_combo.setCurrentIndex(index)
        self._syncing = False

    def _on_model_only_toggled(self, checked: bool) -> None:
        if not self._syncing:
            self._c.model_only_toggled.emit(checked)

    def _show_model_only(self, on: bool) -> None:
        self._syncing = True
        self.model_only_check.setChecked(on)
        self._syncing = False

    def _on_cgms_only_toggled(self, checked: bool) -> None:
        if not self._syncing:
            self._c.cgms_only_toggled.emit(checked)

    def _set_controls_locked(self, locked: bool) -> None:
        """CGMS-only mode: disable every control here that would send a
        now-rejected config write (see PROTOCOL_SPEC.md)."""
        for widget in (
            self.speed_combo,
            self.model_only_check,
        ):
            widget.setEnabled(not locked)

    # ------------------------------------------------------------------
    # Appearance (theme + graph time window)
    # ------------------------------------------------------------------

    def _build_appearance_group(self) -> QGroupBox:
        group = QGroupBox("Appearance")
        form = QFormLayout(group)

        self.theme_combo = QComboBox()
        for value, label in _THEME_LABELS:
            self.theme_combo.addItem(label, value)
        current = app_settings.load_theme()
        self.theme_combo.setCurrentIndex(
            next((i for i, (v, _) in enumerate(_THEME_LABELS) if v == current), 0)
        )
        self.theme_combo.currentIndexChanged.connect(self._on_theme_selected)
        form.addRow("Theme:", self.theme_combo)

        self.window_combo = QComboBox()
        for seconds, label in _WINDOW_CHOICES:
            self.window_combo.addItem(label, seconds)
        saved = int(app_settings.load_pref("view_window_s", 3600))
        self.window_combo.setCurrentIndex(
            next((i for i, (s, _) in enumerate(_WINDOW_CHOICES) if s == saved), 0)
        )
        self.window_combo.currentIndexChanged.connect(self._on_window_selected)
        form.addRow("Graph time window:", self.window_combo)
        return group

    def _on_theme_selected(self, _index: int) -> None:
        mode = self.theme_combo.currentData()
        app_settings.save_theme(mode)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode)
        self._c.theme_changed.emit()

    def _on_window_selected(self, _index: int) -> None:
        app_settings.save_pref("view_window_s", int(self.window_combo.currentData()))
        self._c.view_window_changed.emit()

    # ------------------------------------------------------------------
    # Glucose range thresholds
    # ------------------------------------------------------------------

    def _build_thresholds_group(self) -> QGroupBox:
        group = QGroupBox("Glucose range thresholds")
        form = QFormLayout(group)
        current = app_settings.load()
        self._threshold_spins: dict[str, QDoubleSpinBox] = {}
        for key, caption in _THRESHOLD_ROWS:
            spin = NoWheelDoubleSpinBox()
            spin.setRange(1.0, 600.0)
            spin.setDecimals(0)
            spin.setValue(current[key])
            form.addRow(f"{caption}:", spin)
            self._threshold_spins[key] = spin
        save_btn = QPushButton("Save thresholds")
        save_btn.clicked.connect(self._save_thresholds)
        form.addRow("", save_btn)
        return group

    def _save_thresholds(self) -> None:
        app_settings.save({key: spin.value() for key, spin in self._threshold_spins.items()})
        self._c.thresholds_saved.emit()
