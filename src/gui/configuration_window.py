"""Configuration window: the Person/Sensor selectors, their config buttons and the
mode toggles that used to sit in the main window's bottom bar, plus the glucose
range thresholds editor and a per-person data-source (model vs CSV region) choice.

State still lives on :class:`MainWindow`; this window only hosts the widgets and
forwards every change back to the main window's existing handlers, so the
simulation / BLE behavior is unchanged. The main window keeps Start/Pause/Stop
and the Insert Food/Exercise Now buttons.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from api import protocol
from models import app_settings, food_log_csv
from models.engine import load_csv_window
from models.types import PersonProfile

_THRESHOLD_ROWS = (
    ("tbr2_below", "TBR2 below (mg/dL)"),
    ("tbr1_below", "TBR1 below (mg/dL)"),
    ("tar1_above", "TAR1 above (mg/dL)"),
    ("tar2_above", "TAR2 above (mg/dL)"),
)


class ConfigurationWindow(QWidget):  # pylint: disable=too-many-instance-attributes  # see issue 18
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

        layout_row = QHBoxLayout()
        self.board_layout_btn = QPushButton("Board layout (4 sensors)…")
        self.board_layout_btn.clicked.connect(self._main._open_board_layout)
        layout_row.addWidget(self.board_layout_btn)
        layout_hint = QLabel("Assign a person + sensor to each independent slot")
        layout_hint.setEnabled(False)
        layout_row.addWidget(layout_hint, 1)
        outer.addLayout(layout_row)

        return group

    # ------------------------------------------------------------------
    # Mode toggles
    # ------------------------------------------------------------------

    def _build_modes_group(self) -> QGroupBox:
        group = QGroupBox("Modes")
        box = QVBoxLayout(group)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed:"))
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(0, 1000)
        self.speed_slider.setValue(self._speed_to_slider(self._main._speed_mult))
        self.speed_slider.valueChanged.connect(self._on_speed_slider)
        speed_row.addWidget(self.speed_slider, 1)
        # Type an exact multiplier here — the log-scaled slider alone makes
        # precise values (e.g. x30) fiddly. keyboardTracking off: commit on
        # Enter / focus-out, not on every digit.
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(1, 1000)
        self.speed_spin.setPrefix("x")
        self.speed_spin.setKeyboardTracking(False)
        self.speed_spin.setValue(int(self._main._speed_mult))
        self.speed_spin.setMinimumWidth(72)
        self.speed_spin.valueChanged.connect(self._on_speed_spin)
        speed_row.addWidget(self.speed_spin)
        box.addLayout(speed_row)
        hint = QLabel("x1 = real time · x60 = 1 s per sim-minute · up to x1000")
        hint.setEnabled(False)
        box.addWidget(hint)

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
        self.model_only_check.toggled.connect(self._main._on_model_only_toggled)
        box.addWidget(self.model_only_check)
        self.cgms_only_check = QCheckBox("CGMS Only (standard CGM stream only)")
        self.cgms_only_check.toggled.connect(self._main._on_cgms_only_toggled)
        box.addWidget(self.cgms_only_check)
        return group

    # Log map so the 0..1000 slider gives fine control at low speeds:
    # 0 -> x1, 333 -> x10, 667 -> x100, 1000 -> x1000.
    @staticmethod
    def _slider_to_speed(s: int) -> int:
        return max(1, min(1000, round(10 ** (3 * s / 1000))))

    @staticmethod
    def _speed_to_slider(mult: float) -> int:
        mult = max(1.0, min(1000.0, float(mult)))
        return max(0, min(1000, round(1000 * math.log10(mult) / 3)))

    def _on_speed_slider(self, s: int) -> None:
        mult = self._slider_to_speed(s)
        self.speed_spin.blockSignals(True)
        self.speed_spin.setValue(mult)
        self.speed_spin.blockSignals(False)
        self._main._on_speed_changed(float(mult))

    def _on_speed_spin(self, mult: int) -> None:
        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(self._speed_to_slider(mult))
        self.speed_slider.blockSignals(False)
        self._main._on_speed_changed(float(mult))

    def _on_comm_profile_changed(self, _index: int) -> None:
        """Write the chosen BLE comm profile to every connected board, then let the
        board drop the link and re-advertise, and reconnect to it automatically."""
        dexcom = bool(self.comm_profile_combo.currentData())
        bt = self._main._ensure_bluetooth_window()
        sessions = bt.sessions()
        if not sessions:
            return
        payload = protocol.encode_comm_profile(dexcom)
        for address, session in list(sessions.items()):
            session.queue_write("comm_profile", payload)
            bt.reconnect(address)

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
        hint = QLabel(
            "Pick the CSV file and 24 h window in the CSV Analysis window, "
            "then assign it to this patient there."
        )
        hint.setWordWrap(True)
        hint.setEnabled(False)
        box.addWidget(hint)

        send_row = QHBoxLayout()
        self._send_csv_btn = QPushButton("Send CSV to Board")
        self._send_csv_btn.clicked.connect(self._send_csv_to_board)
        send_row.addWidget(self._send_csv_btn)
        self._send_csv_status = QLabel("")
        self._send_csv_status.setWordWrap(True)
        send_row.addWidget(self._send_csv_status, 1)
        box.addLayout(send_row)

        self._load_data_source()
        return group

    def _current_person(self) -> PersonProfile | None:
        return self.person_combo.currentData()

    def reload_data_source(self) -> None:
        """Re-sync the data-source group from the selected person's profile.

        Public entry point for MainWindow to call after a profile change made
        outside this window (the person combo keeps its selection, so
        currentIndexChanged does not fire on its own)."""
        self._load_data_source()

    def _load_data_source(self) -> None:
        person = self._current_person()
        enabled = person is not None
        for w in (self._src_model_radio, self._src_csv_radio):
            w.setEnabled(enabled)
        if person is None:
            self._send_csv_btn.setEnabled(False)
            return
        self._src_model_radio.blockSignals(True)
        self._src_csv_radio.blockSignals(True)
        is_csv = getattr(person, "data_source", "model") == "csv"
        self._src_csv_radio.setChecked(is_csv)
        self._src_model_radio.setChecked(not is_csv)
        self._src_model_radio.blockSignals(False)
        self._src_csv_radio.blockSignals(False)

        lines = [person.csv_path or "— no CSV region assigned —"]
        if getattr(person, "csv_window_start_iso", None):
            lines.append(f"window start: {person.csv_window_start_iso}")
        food_log = getattr(person, "food_log_path", None)
        if not food_log and person.csv_path:
            food_log = food_log_csv.matching_food_log_path(person.csv_path)
        if food_log:
            lines.append(f"food log (auto): {food_log}")
        self._csv_path_label.setText("\n".join(lines))
        self._send_csv_btn.setEnabled(is_csv and bool(person.csv_path))

    def _save_data_source(self) -> None:
        person = self._current_person()
        if person is None:
            return
        person.data_source = "csv" if self._src_csv_radio.isChecked() else "model"
        self._main._on_profiles_changed()
        self._load_data_source()

    def _send_csv_to_board(self) -> None:
        """Build the glucose + food-log tracks for the selected CSV patient and
        upload them to a connected board, then set its data source to CSV."""
        person = self._current_person()
        if person is None or getattr(person, "data_source", "model") != "csv":
            QMessageBox.information(self, "Send CSV", "Select a CSV-backed patient first.")
            return

        samples, interval_s, foodlog = load_csv_window(person)
        if not samples:
            QMessageBox.warning(
                self,
                "Send CSV",
                "Could not build the 24 h window — re-assign it in CSV Analysis.",
            )
            return

        bt_window = self._main._ensure_bluetooth_window()
        sessions = bt_window.sessions()
        if not sessions:
            QMessageBox.warning(self, "Send CSV", "No connected board.")
            return
        if len(sessions) == 1:
            address = next(iter(sessions))
        else:
            names = {bt_window.display_name(a): a for a in sessions}
            name, ok = QInputDialog.getItem(
                self, "Send CSV", "Target board:", list(names), 0, False
            )
            if not ok:
                return
            address = names[name]
        session = sessions[address]

        uploads = protocol.build_csv_uploads(
            samples, interval_s, foodlog, person.csv_window_start_iso
        )

        try:
            session.csv_upload_progress.disconnect(self._on_csv_progress)
            session.csv_upload_finished.disconnect(self._on_csv_finished)
        except TypeError:
            pass
        session.csv_upload_progress.connect(self._on_csv_progress)
        session.csv_upload_finished.connect(self._on_csv_finished)

        self._send_csv_status.setText("Uploading CSV…")
        self._send_csv_btn.setEnabled(False)
        session.start_csv_upload(uploads)
        # Switch the board to CSV playback once the bytes are in (see
        # _on_csv_finished); also remember which session for that write.
        self._csv_upload_session = session

    def _on_csv_progress(self, _address: str, sent: int, total: int) -> None:
        self._send_csv_status.setText(f"Uploading CSV… {sent}/{total} B")

    def _on_csv_finished(self, _address: str, ok: bool, message: str) -> None:
        self._send_csv_btn.setEnabled(True)
        if ok:
            session = getattr(self, "_csv_upload_session", None)
            if session is not None:
                session.queue_write("data_source", protocol.encode_data_source(True))
                from gui.device_target import restart_board

                restart_board(session)
            self._send_csv_status.setText(f"✓ {message} — board set to CSV playback")
        else:
            self._send_csv_status.setText(f"⚠ Upload failed: {message}")
