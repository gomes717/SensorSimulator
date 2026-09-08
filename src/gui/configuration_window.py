"""Configuration window: the Person/Sensor selectors, their config buttons and the
mode toggles that used to sit in the main window's bottom bar, plus the glucose
range thresholds editor and a per-person data-source (model vs CSV region) choice.

Simulation / BLE state lives on :class:`MainWindow`; this window only hosts the
widgets. It talks to the app through a :class:`ConfigController` (a typed signal
surface, issue 18) and one injected ``bluetooth_provider`` callable for the CSV
upload path — it no longer takes ``MainWindow`` or reaches its private members.
The main window keeps Start/Pause/Stop and the Insert Food/Exercise Now buttons.
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
from gui.config_controller import ConfigController
from gui.device_target import restart_board
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

    def __init__(
        self,
        controller: ConfigController,
        bluetooth_provider: Callable[[], object],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuration")
        self.resize(460, 520)
        self._c = controller
        self._bluetooth_provider = bluetooth_provider
        # Set while the window is repopulating a combo / re-syncing a widget from
        # a controller signal, so the widget's own change handler does not echo
        # the change straight back to the app.
        self._syncing = False

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_selectors_group())
        layout.addWidget(self._build_modes_group())
        layout.addWidget(self._build_thresholds_group())
        layout.addWidget(self._build_data_source_group())
        layout.addStretch(1)

        # The combos are left empty here on purpose: MainWindow builds this
        # window before its graphs exist, then fires profiles_changed once the
        # rest of __init__ is done (the first repopulate can emit person_selected,
        # which restarts the engine and touches the graphs).

        # app -> widget: re-sync widgets after a state change made outside this window.
        controller.profiles_changed.connect(self._repopulate_combos)
        controller.speed_display_changed.connect(self._show_speed)
        controller.comm_profile_display_changed.connect(self._show_comm_profile)
        controller.model_only_display_changed.connect(self._show_model_only)
        controller.controls_locked.connect(self._set_controls_locked)
        controller.data_source_display_changed.connect(self._load_data_source)

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
        self.person_combo.currentIndexChanged.connect(self._on_person_combo_changed)
        person_row.addWidget(self.person_combo, 1)
        outer.addLayout(person_row)

        person_btns = QHBoxLayout()
        self.person_configure_btn = QPushButton("Configure…")
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
        sensor_row.addWidget(QLabel("Sensor:"))
        self.sensor_combo = QComboBox()
        self.sensor_combo.setMinimumWidth(160)
        self.sensor_combo.currentIndexChanged.connect(self._on_sensor_combo_changed)
        sensor_row.addWidget(self.sensor_combo, 1)
        self.sensor_configure_btn = QPushButton("Configure…")
        self.sensor_configure_btn.clicked.connect(lambda: self._c.editor_requested.emit("sensor"))
        sensor_row.addWidget(self.sensor_configure_btn)
        outer.addLayout(sensor_row)

        layout_row = QHBoxLayout()
        self.board_layout_btn = QPushButton("Board layout (4 sensors)…")
        self.board_layout_btn.clicked.connect(lambda: self._c.editor_requested.emit("board_layout"))
        layout_row.addWidget(self.board_layout_btn)
        layout_hint = QLabel("Assign a person + sensor to each independent slot")
        layout_hint.setEnabled(False)
        layout_row.addWidget(layout_hint, 1)
        outer.addLayout(layout_row)

        return group

    def _on_person_combo_changed(self, _index: int) -> None:
        if self._syncing:
            return
        self._c.person_selected.emit(self.person_combo.currentData())
        self._load_data_source()

    def _on_sensor_combo_changed(self, _index: int) -> None:
        if self._syncing:
            return
        self._c.sensor_selected.emit(self.sensor_combo.currentData())

    # ------------------------------------------------------------------
    # Combo population (moved here from MainWindow with issue 18 — the widgets
    # live here, so the "keep the selection / default to the first profile"
    # logic does too).
    # ------------------------------------------------------------------

    def _repopulate_combos(self) -> None:
        self._repopulate_one(self.person_combo, self._c.person_profiles, self._c.active_person)
        self._repopulate_one(self.sensor_combo, self._c.sensor_profiles, self._c.active_sensor)
        self._load_data_source()

    def _repopulate_one(self, combo: QComboBox, profiles: list, active) -> None:
        """Rebuild *combo* from *profiles*, keeping the current selection if it
        still exists; on first load (nothing ever selected) default to the first
        profile so the graph shows data right away instead of sitting empty."""
        current_name = active.name if active is not None else None
        self._syncing = True
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("(none)", None)
        select_index = 0
        for i, profile in enumerate(profiles):
            combo.addItem(profile.name, profile)
            if profile.name == current_name:
                select_index = i + 1
        if select_index == 0 and active is None and profiles:
            select_index = 1
        combo.setCurrentIndex(select_index)
        combo.blockSignals(False)
        self._syncing = False
        # If the selection actually moved (profile deleted, or first-load
        # default), tell the app once — signals were blocked above.
        chosen = combo.currentData()
        if chosen is not active:
            sig = self._c.person_selected if combo is self.person_combo else self._c.sensor_selected
            sig.emit(chosen)

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
        self.speed_slider.setValue(self._speed_to_slider(self._c.speed_mult))
        self.speed_slider.valueChanged.connect(self._on_speed_slider)
        speed_row.addWidget(self.speed_slider, 1)
        # Type an exact multiplier here — the log-scaled slider alone makes
        # precise values (e.g. x30) fiddly. keyboardTracking off: commit on
        # Enter / focus-out, not on every digit.
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(1, 1000)
        self.speed_spin.setPrefix("x")
        self.speed_spin.setKeyboardTracking(False)
        self.speed_spin.setValue(int(self._c.speed_mult))
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
        self.model_only_check.toggled.connect(self._on_model_only_toggled)
        box.addWidget(self.model_only_check)
        self.cgms_only_check = QCheckBox("CGMS Only (standard CGM stream only)")
        self.cgms_only_check.toggled.connect(self._on_cgms_only_toggled)
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
        if not self._syncing:
            self._c.speed_change_requested.emit(float(mult))

    def _on_speed_spin(self, mult: int) -> None:
        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(self._speed_to_slider(mult))
        self.speed_slider.blockSignals(False)
        if not self._syncing:
            self._c.speed_change_requested.emit(float(mult))

    def _show_speed(self, mult: float) -> None:
        """Move the slider + spin to *mult* without emitting a change back."""
        self._syncing = True
        self.speed_slider.setValue(self._speed_to_slider(mult))
        self.speed_spin.setValue(int(max(1.0, min(1000.0, mult))))
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
            self.speed_slider,
            self.speed_spin,
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
        self._c.thresholds_saved.emit()

    # ------------------------------------------------------------------
    # Per-person data source (model vs CSV region)
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
        return group

    def _current_person(self) -> PersonProfile | None:
        return self.person_combo.currentData()

    def reload_data_source(self) -> None:
        """Back-compat alias kept for callers outside this window."""
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
        if person is None or self._syncing:
            return
        person.data_source = "csv" if self._src_csv_radio.isChecked() else "model"
        self._c.data_source_edited.emit()
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

        bt_window = self._bluetooth_provider()
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
                restart_board(session)
            self._send_csv_status.setText(f"✓ {message} — board set to CSV playback")
        else:
            self._send_csv_status.setText(f"⚠ Upload failed: {message}")
