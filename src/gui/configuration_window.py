"""Configuration window: the Person/Sensor selectors, their config buttons, the
mode toggles that used to sit in the main window's bottom bar, and the glucose
range thresholds editor.

Simulation / BLE state lives on :class:`MainWindow`; this window only hosts the
widgets and talks to the app through a :class:`ConfigController` (a typed signal
surface, issue 18) — it no longer takes ``MainWindow`` or reaches its private
members. The per-person data source (model vs CSV region) moved to the Person
Configuration window (issue 16), next to the model it replaces.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
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
from gui.widgets import NoWheelDoubleSpinBox
from models import app_settings

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
        inner.addWidget(self._build_selectors_group())
        inner.addWidget(self._build_modes_group())
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
        controller.comm_profile_display_changed.connect(self._show_comm_profile)
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
        height = body.heightForWidth(width - chrome) or body.sizeHint().height()
        self.resize(width, min(height + chrome, int(available.height() * 0.9)))

    # ------------------------------------------------------------------
    # Selectors + config buttons
    # ------------------------------------------------------------------

    def _build_selectors_group(self) -> QGroupBox:
        group = QGroupBox("Patient / sensor")
        outer = QVBoxLayout(group)

        # No Person/Sensor combos: each editor owns its own profile list, and
        # which profile a board slot runs is decided by that editor's own
        # "Send to Board" (see MainWindow.record_slot_assignment).
        person_btns = QHBoxLayout()
        self.person_configure_btn = QPushButton("Person…")
        self.person_configure_btn.clicked.connect(lambda: self._c.editor_requested.emit("person"))
        person_btns.addWidget(self.person_configure_btn)
        self.food_btn = QPushButton("Food…")
        self.food_btn.clicked.connect(lambda: self._c.editor_requested.emit("food"))
        person_btns.addWidget(self.food_btn)
        self.exercise_btn = QPushButton("Exercise…")
        self.exercise_btn.clicked.connect(lambda: self._c.editor_requested.emit("exercise"))
        person_btns.addWidget(self.exercise_btn)
        outer.addLayout(person_btns)

        sensor_row = QHBoxLayout()
        self.sensor_configure_btn = QPushButton("Sensor…")
        self.sensor_configure_btn.clicked.connect(lambda: self._c.editor_requested.emit("sensor"))
        sensor_row.addWidget(self.sensor_configure_btn)
        sensor_row.addStretch(1)
        outer.addLayout(sensor_row)

        return group

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

        comm_row = QHBoxLayout()
        comm_row.addWidget(QLabel("Communication type:"))
        self.comm_profile_combo = QComboBox()
        self.comm_profile_combo.addItem("SIG CGMS (standard)", False)
        self.comm_profile_combo.addItem("Dexcom-style (basic)", True)
        self.comm_profile_combo.currentIndexChanged.connect(self._on_comm_profile_changed)
        comm_row.addWidget(self.comm_profile_combo, 1)
        box.addLayout(comm_row)
        comm_hint = QLabel(
            "Switching re-advertises the board; the app drops and reconnects automatically (~3 s)."
        )
        comm_hint.setWordWrap(True)
        comm_hint.setEnabled(False)
        box.addWidget(comm_hint)

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

    def _on_comm_profile_changed(self, _index: int) -> None:
        if self._syncing:
            return
        self._c.comm_profile_toggled.emit(bool(self.comm_profile_combo.currentData()))

    def _show_comm_profile(self, dexcom: bool) -> None:
        self._syncing = True
        self.comm_profile_combo.setCurrentIndex(1 if dexcom else 0)
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
            self.person_configure_btn,
            self.food_btn,
            self.exercise_btn,
            self.sensor_configure_btn,
            self.speed_combo,
            self.model_only_check,
        ):
            widget.setEnabled(not locked)

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
