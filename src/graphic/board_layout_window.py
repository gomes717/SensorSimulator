"""Board Layout window: assign a saved Person + Sensor profile to each of the
board's independent sensor slots, then push the whole layout in one action.

The multi-sensor firmware (``PROTOCOL_SPEC.md`` §7) runs up to
:data:`models.board_layout.MAX_SLOTS` fully independent slots, each with its own
model/params/noise/schedule *or* a CSV. This window is the app side of that:
pick "slot *i* = person X + sensor Y" for each slot, hit **Send layout to
Board**, and :meth:`services.ble_session.BleSession.send_board_layout` writes the
sensor-select cursor + every per-slot characteristic (and uploads a per-slot CSV
when the assigned person is CSV-backed) over one connection to the board's
shared config service.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from api import protocol
from graphic.bluetooth_window import BluetoothWindow
from models import board_layout as board_layout_model
from models.board_layout import MAX_SLOTS, BoardLayout
from models.engine import load_csv_window
from models.types import PersonProfile, SensorProfile

_UNUSED = "— unused —"


class BoardLayoutWindow(QWidget):
    """Per-slot Person/Sensor assignment + a one-shot "Send layout to Board"."""

    def __init__(
        self,
        person_profiles: list[PersonProfile],
        sensor_profiles: list[SensorProfile],
        layout: BoardLayout,
        on_change: Callable[[], None],
        get_bluetooth_window: Callable[[], BluetoothWindow],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Board Layout")
        self.resize(560, 360)

        self._persons = person_profiles
        self._sensors = sensor_profiles
        self._layout = layout
        self._on_change = on_change
        self._get_bt = get_bluetooth_window
        self._session = None  # tracks which session's signals we're connected to
        self._sending = False

        layout_box = QVBoxLayout(self)

        hint = QLabel(
            "Each slot is an independent sensor: its own model/parameters/noise/"
            "schedule, or its own CSV (a CSV-backed person uploads its recorded "
            "window to that slot). The simulation clock and speed are shared. "
            "The whole layout is pushed to every slot over one connection — no "
            "need to pick a device. Connect to all 4 identities in the Bluetooth "
            "window to see every slot's stream."
        )
        hint.setWordWrap(True)
        hint.setEnabled(False)
        layout_box.addWidget(hint)

        self._conn_status = QLabel("")
        self._conn_status.setWordWrap(True)
        layout_box.addWidget(self._conn_status)

        grid_group = QGroupBox(f"Slots (up to {MAX_SLOTS})")
        grid = QGridLayout(grid_group)
        grid.addWidget(QLabel("<b>Slot</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Person</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Sensor noise</b>"), 0, 2)
        grid.addWidget(QLabel("<b>Status</b>"), 0, 3)

        self._person_combos: list[QComboBox] = []
        self._sensor_combos: list[QComboBox] = []
        self._row_status: list[QLabel] = []
        for i in range(MAX_SLOTS):
            grid.addWidget(QLabel(f"Sensor {i + 1}"), i + 1, 0)

            person_combo = QComboBox()
            person_combo.currentIndexChanged.connect(self._persist)
            person_combo.currentIndexChanged.connect(self._refresh_row_status)
            grid.addWidget(person_combo, i + 1, 1)
            self._person_combos.append(person_combo)

            sensor_combo = QComboBox()
            sensor_combo.currentIndexChanged.connect(self._persist)
            grid.addWidget(sensor_combo, i + 1, 2)
            self._sensor_combos.append(sensor_combo)

            status = QLabel("")
            status.setWordWrap(True)
            grid.addWidget(status, i + 1, 3)
            self._row_status.append(status)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(3, 1)
        layout_box.addWidget(grid_group)

        send_row = QHBoxLayout()
        self._send_btn = QPushButton("Send layout to Board")
        self._send_btn.clicked.connect(self._send)
        send_row.addWidget(self._send_btn)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        send_row.addWidget(self._status, 1)
        layout_box.addLayout(send_row)
        layout_box.addStretch(1)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._refresh_conn_status)
        self._poll.start(2000)
        self.reload_profiles()
        self._refresh_conn_status()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._refresh_conn_status()
        super().showEvent(event)

    # ------------------------------------------------------------------
    # Connection status / session pick
    # ------------------------------------------------------------------

    def _ready_sessions(self) -> list:
        """Connected sessions that have finished discovering the config service."""
        return [s for s in self._get_bt().sessions().values() if s.exposes("person")]

    def _pick_session(self):
        """Any connected board session. The layout push writes the sensor-select
        cursor per slot over one connection to the shared config service, so
        which identity it runs over does not matter."""
        ready = self._ready_sessions()
        return ready[0] if ready else None

    def _refresh_conn_status(self) -> None:
        total = len(self._get_bt().sessions())
        ready = len(self._ready_sessions())
        if ready:
            self._conn_status.setText(f"Board: connected ({ready} session(s)).")
        elif total:
            self._conn_status.setText("Board: connecting… wait for a session to finish, then Send.")
        else:
            self._conn_status.setText(
                "Board: not connected — open the Bluetooth window and connect to a sensor first."
            )
        if not self._sending:
            self._send_btn.setEnabled(ready > 0)

    # ------------------------------------------------------------------
    # Population / persistence
    # ------------------------------------------------------------------

    def reload_profiles(self) -> None:
        """Repopulate every combo from the current profile lists + saved layout.

        Public so MainWindow can call it after a profile add/rename/delete."""
        person_names = [p.name for p in self._persons]
        sensor_names = [s.name for s in self._sensors]
        for i in range(MAX_SLOTS):
            assign = self._layout.slots[i]
            self._fill_combo(self._person_combos[i], person_names, assign.person)
            self._fill_combo(self._sensor_combos[i], sensor_names, assign.sensor)
        self._refresh_row_status()

    @staticmethod
    def _fill_combo(combo: QComboBox, names: list[str], selected: str | None) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(_UNUSED, None)
        for name in names:
            combo.addItem(name, name)
        idx = combo.findData(selected) if selected else 0
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _persist(self) -> None:
        for i in range(MAX_SLOTS):
            self._layout.slots[i].person = self._person_combos[i].currentData()
            self._layout.slots[i].sensor = self._sensor_combos[i].currentData()
        self._on_change()

    def _person_by_name(self, name: str | None) -> PersonProfile | None:
        return next((p for p in self._persons if p.name == name), None) if name else None

    def _sensor_by_name(self, name: str | None) -> SensorProfile | None:
        return next((s for s in self._sensors if s.name == name), None) if name else None

    def _refresh_row_status(self) -> None:
        for i in range(MAX_SLOTS):
            person = self._person_by_name(self._person_combos[i].currentData())
            if person is None:
                self._row_status[i].setText("unused")
                continue
            if getattr(person, "data_source", "model") == "csv":
                samples, _interval, _food = load_csv_window(person)
                if samples:
                    self._row_status[i].setText(f"CSV · {len(samples)} rows")
                else:
                    self._row_status[i].setText("CSV window missing — fix in CSV Analysis")
            else:
                model = getattr(person.model_id, "name", str(person.model_id))
                self._row_status[i].setText(f"model · {model.title()}")

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    def _build_slots(self) -> tuple[list[dict], list[str]]:
        """Turn the current assignments into send_board_layout() entries.

        Returns ``(slots, errors)`` — *slots* is empty when *errors* is not."""
        slots: list[dict] = []
        errors: list[str] = []
        for i in range(MAX_SLOTS):
            person = self._person_by_name(self._person_combos[i].currentData())
            if person is None:
                continue
            sensor = self._sensor_by_name(self._sensor_combos[i].currentData())
            is_csv = getattr(person, "data_source", "model") == "csv"

            writes: list[tuple[str, bytes]] = [
                ("person", protocol.encode_person_config(person.model_id, person.params)),
            ]
            if sensor is not None:
                writes.append(
                    ("sensor", protocol.encode_sensor_config(sensor.sensor_id, sensor.params))
                )
            writes.append(("data_source", protocol.encode_data_source(is_csv)))
            writes.append(("food", protocol.encode_clear_food()))
            for ev in person.food_events:
                writes.append(("food", protocol.encode_food_event(ev)))
            writes.append(("exercise", protocol.encode_clear_exercise()))
            for ev in person.exercise_events:
                writes.append(("exercise", protocol.encode_exercise_event(ev)))

            csv_entry = None
            if is_csv:
                samples, interval_s, foodlog = load_csv_window(person)
                if not samples or not person.csv_window_start_iso:
                    errors.append(f"Slot {i + 1} ({person.name}): CSV window not set")
                    continue
                base_epoch = int(
                    datetime.fromisoformat(person.csv_window_start_iso).timestamp()
                )
                uploads = [{
                    "track": protocol.CSV_TRACK_GLUCOSE,
                    "blob": protocol.build_glucose_track([float(s) for s in samples]),
                    "row_count": len(samples),
                    "base_epoch_s": base_epoch,
                    "interval_s": interval_s,
                }]
                if foodlog:
                    uploads.append({
                        "track": protocol.CSV_TRACK_FOODLOG,
                        "blob": protocol.build_foodlog_track(foodlog),
                        "row_count": len(foodlog),
                        "base_epoch_s": base_epoch,
                        "interval_s": 0,
                    })
                csv_entry = {"uploads": uploads}

            slots.append({"slot": i, "writes": writes, "csv": csv_entry})
        return (slots, errors) if not errors else ([], errors)

    def _send(self) -> None:
        session = self._pick_session()
        if session is None:
            QMessageBox.warning(
                self, "Board Layout",
                "No connected board. Open the Bluetooth window and connect to a sensor first.",
            )
            return

        slots, errors = self._build_slots()
        if errors:
            QMessageBox.warning(self, "Board Layout", "\n".join(errors))
            return
        if not slots:
            QMessageBox.information(self, "Board Layout", "No slots assigned.")
            return

        if self._session is not None:
            for sig in ("board_layout_progress", "board_layout_finished"):
                try:
                    getattr(self._session, sig).disconnect()
                except TypeError:
                    pass
        self._session = session
        session.board_layout_progress.connect(self._on_progress)
        session.board_layout_finished.connect(self._on_finished)

        self._sending = True
        self._send_btn.setEnabled(False)
        self._status.setText(f"Sending {len(slots)} slot(s)…")
        session.send_board_layout(slots)

    def _on_progress(self, _address: str, done: int, total: int) -> None:
        self._status.setText(f"Sending… slot {done}/{total}")

    def _on_finished(self, _address: str, ok: bool, message: str) -> None:
        self._sending = False
        self._status.setText(("✓ " if ok else "⚠ ") + message)
        self._refresh_conn_status()
