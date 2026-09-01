"""Configuration window: the Person/Sensor selectors, their config buttons and the
mode toggles that used to sit in the main window's bottom bar, plus the glucose
range thresholds editor and a per-person data-source (model vs CSV region) choice.

State still lives on :class:`MainWindow`; this window only hosts the widgets and
forwards every change back to the main window's existing handlers, so the
simulation / BLE behavior is unchanged. The main window keeps Start/Pause/Stop
and the Insert Food/Exercise Now buttons.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from models import app_settings
from models.types import PersonProfile

_THRESHOLD_ROWS = (
    ("tbr2_below", "TBR2 below (mg/dL)"),
    ("tbr1_below", "TBR1 below (mg/dL)"),
    ("tar1_above", "TAR1 above (mg/dL)"),
    ("tar2_above", "TAR2 above (mg/dL)"),
)


class ConfigurationWindow(QWidget):
    """Selectors, mode toggles, range thresholds and the per-person data source."""

    def __init__(self, main, on_thresholds_changed: Callable[[], None], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuration")
        self.resize(460, 520)
        self._main = main
        self._on_thresholds_changed = on_thresholds_changed

        layout = QVBoxLayout(self)

        layout.addWidget(self._build_selectors_group())
        layout.addWidget(self._build_modes_group())
        layout.addWidget(self._build_thresholds_group())
        layout.addWidget(self._build_data_source_group())
        layout.addStretch(1)

    # ------------------------------------------------------------------
    # Selectors + config buttons
    # ------------------------------------------------------------------

    def _build_selectors_group(self) -> QGroupBox:
        group = QGroupBox("Patient / sensor")
        outer = QVBoxLayout(group)

        person_row = QHBoxLayout()
        person_row.addWidget(QLabel("Person:"))
        self.person_combo = QComboBox()
        self.person_combo.setMinimumWidth(160)
        self.person_combo.currentIndexChanged.connect(self._main._on_person_selected)
        self.person_combo.currentIndexChanged.connect(self._load_data_source)
        person_row.addWidget(self.person_combo, 1)
        outer.addLayout(person_row)

        person_btns = QHBoxLayout()
        self.person_configure_btn = QPushButton("Configure…")
        self.person_configure_btn.clicked.connect(self._main._open_person_config)
        person_btns.addWidget(self.person_configure_btn)
        self.food_btn = QPushButton("Food…")
        self.food_btn.clicked.connect(self._main._open_food_config)
        person_btns.addWidget(self.food_btn)
        self.exercise_btn = QPushButton("Exercise…")
        self.exercise_btn.clicked.connect(self._main._open_exercise_config)
        person_btns.addWidget(self.exercise_btn)
        outer.addLayout(person_btns)

        sensor_row = QHBoxLayout()
        sensor_row.addWidget(QLabel("Sensor:"))
        self.sensor_combo = QComboBox()
        self.sensor_combo.setMinimumWidth(160)
        self.sensor_combo.currentIndexChanged.connect(self._main._on_sensor_selected)
        sensor_row.addWidget(self.sensor_combo, 1)
        self.sensor_configure_btn = QPushButton("Configure…")
        self.sensor_configure_btn.clicked.connect(self._main._open_sensor_config)
        sensor_row.addWidget(self.sensor_configure_btn)
        outer.addLayout(sensor_row)

        return group

    # ------------------------------------------------------------------
    # Mode toggles
    # ------------------------------------------------------------------

    def _build_modes_group(self) -> QGroupBox:
        group = QGroupBox("Modes")
        box = QVBoxLayout(group)
        self.fast_mode_check = QCheckBox("Fast mode (1 s = 1 sim-minute)")
        self.fast_mode_check.toggled.connect(self._main._on_fast_mode_toggled)
        box.addWidget(self.fast_mode_check)
        self.model_only_check = QCheckBox("Model Only (no device)")
        self.model_only_check.toggled.connect(self._main._on_model_only_toggled)
        box.addWidget(self.model_only_check)
        self.cgms_only_check = QCheckBox("CGMS Only (standard CGM stream only)")
        self.cgms_only_check.toggled.connect(self._main._on_cgms_only_toggled)
        box.addWidget(self.cgms_only_check)
        return group

    # ------------------------------------------------------------------
    # Glucose range thresholds
    # ------------------------------------------------------------------

    def _build_thresholds_group(self) -> QGroupBox:
        group = QGroupBox("Glucose range thresholds")
        form = QFormLayout(group)
        current = app_settings.load()
        self._threshold_spins: dict[str, QDoubleSpinBox] = {}
        for key, caption in _THRESHOLD_ROWS:
            spin = QDoubleSpinBox()
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
        self._on_thresholds_changed()

    # ------------------------------------------------------------------
    # Per-person data source (model vs CSV region) — stored only, no playback yet
    # ------------------------------------------------------------------

    def _build_data_source_group(self) -> QGroupBox:
        group = QGroupBox("Data source for selected person")
        box = QVBoxLayout(group)
        self._src_model_radio = QRadioButton("Physiological model (parameters)")
        self._src_csv_radio = QRadioButton("CSV region (24 h window)")
        self._src_model_radio.toggled.connect(self._save_data_source)
        box.addWidget(self._src_model_radio)
        box.addWidget(self._src_csv_radio)
        self._csv_path_label = QLabel("—")
        self._csv_path_label.setWordWrap(True)
        box.addWidget(self._csv_path_label)
        hint = QLabel("Pick the CSV file and 24 h window in the CSV Analysis window. "
                      "CSV playback is not active yet (see docs/TODO.md).")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        box.addWidget(hint)
        self._load_data_source()
        return group

    def _current_person(self) -> PersonProfile | None:
        return self.person_combo.currentData()

    def _load_data_source(self) -> None:
        person = self._current_person()
        enabled = person is not None
        for w in (self._src_model_radio, self._src_csv_radio):
            w.setEnabled(enabled)
        if person is None:
            return
        self._src_model_radio.blockSignals(True)
        self._src_csv_radio.blockSignals(True)
        is_csv = getattr(person, "data_source", "model") == "csv"
        self._src_csv_radio.setChecked(is_csv)
        self._src_model_radio.setChecked(not is_csv)
        self._src_model_radio.blockSignals(False)
        self._src_csv_radio.blockSignals(False)
        self._csv_path_label.setText(person.csv_path or "— no CSV region assigned —")

    def _save_data_source(self) -> None:
        person = self._current_person()
        if person is None:
            return
        person.data_source = "csv" if self._src_csv_radio.isChecked() else "model"
        self._main._on_profiles_changed()
