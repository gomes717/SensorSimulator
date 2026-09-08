"""Window for editing the active person's recurring-daily exercise schedule."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTime
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from api import protocol
from graphic.bluetooth_window import BluetoothWindow
from graphic.device_target import DeviceTargetBar, await_send_confirmation, restart_board
from models.types import ExerciseEvent, PersonProfile


class ExerciseConfigWindow(QWidget):
    """Add/remove recurring-daily exercise bouts for whichever person is currently active.

    Only the Roy/Parker and Deichmann models actually react to exercise —
    Cambridge and UVA/Padova have no exercise term, so entries here are simply
    ignored for those models (see models/engine.py's per-model step adapters).
    """

    def __init__(
        self,
        get_active_person: Callable[[], PersonProfile | None],
        on_change: Callable[[], None],
        get_bluetooth_window: Callable[[], BluetoothWindow],
        parent=None,
    ) -> None:
        """Build the exercise table, add-row form, and Save/Send controls."""
        super().__init__(parent)
        self.setWindowTitle("Exercise Configuration")
        self.resize(480, 420)

        self._get_active_person = get_active_person
        self._on_change = on_change
        self._read_session = None  # tracks which BleSession config_read is currently connected to

        layout = QVBoxLayout(self)
        self._active_label = QLabel()
        layout.addWidget(self._active_label)
        self._target_bar = DeviceTargetBar(get_bluetooth_window)
        layout.addWidget(self._target_bar)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Time of day", "Duration (min)", "Intensity (%)"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table, 1)

        add_row = QHBoxLayout()
        self._time_edit = QTimeEdit(QTime(18, 0))
        add_row.addWidget(QLabel("Time:"))
        add_row.addWidget(self._time_edit)
        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(1, 300)
        self._duration_spin.setValue(30)
        add_row.addWidget(QLabel("Duration (min):"))
        add_row.addWidget(self._duration_spin)
        self._intensity_spin = QDoubleSpinBox()
        self._intensity_spin.setRange(0.0, 100.0)
        self._intensity_spin.setValue(50.0)
        add_row.addWidget(QLabel("Intensity (%):"))
        add_row.addWidget(self._intensity_spin)
        self._add_btn = QPushButton("Add")
        self._add_btn.clicked.connect(self._add_event)
        add_row.addWidget(self._add_btn)
        layout.addLayout(add_row)

        buttons = QHBoxLayout()
        self._remove_btn = QPushButton("Remove Selected")
        self._remove_btn.clicked.connect(self._remove_selected)
        buttons.addWidget(self._remove_btn)
        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._save)
        buttons.addWidget(self._save_btn)
        self._send_btn = QPushButton("Send to Board")
        self._send_btn.clicked.connect(self._send_to_board)
        buttons.addWidget(self._send_btn)
        self._read_btn = QPushButton("Read from Board")
        self._read_btn.clicked.connect(self._read_from_board)
        buttons.addWidget(self._read_btn)
        layout.addLayout(buttons)

        self._send_status = QLabel("")
        layout.addWidget(self._send_status)

        self._edit_widgets = [
            self._time_edit,
            self._duration_spin,
            self._intensity_spin,
            self._add_btn,
            self._remove_btn,
            self._save_btn,
            self._send_btn,
            self._read_btn,
        ]
        self._events: list[ExerciseEvent] = []
        self.refresh()

    def refresh(self) -> None:
        """Reload the table from the currently active person's saved exercise events."""
        person = self._get_active_person()
        is_csv = person is not None and getattr(person, "data_source", "model") == "csv"
        if person is None:
            self._active_label.setText("No active person selected — pick one in the main window.")
            self._events = []
        elif is_csv:
            self._active_label.setText(
                f"{person.name} replays a recorded CSV — exercise schedule disabled."
            )
            self._events = list(person.exercise_events)
        else:
            self._active_label.setText(f"Editing exercise for: {person.name}")
            self._events = list(person.exercise_events)
        tip = (
            "Disabled: this patient replays a recorded CSV window. The recurring "
            "exercise schedule is not used for a CSV-backed patient."
            if is_csv
            else ""
        )
        for w in self._edit_widgets:
            w.setEnabled(not is_csv)
            w.setToolTip(tip)
        self._table.setEnabled(not is_csv)
        self._redraw_table()

    def showEvent(self, event) -> None:
        """Reload from the active person every time the window is (re)shown."""
        self.refresh()
        super().showEvent(event)

    def _redraw_table(self) -> None:
        self._table.setRowCount(len(self._events))
        for row, ev in enumerate(self._events):
            time_str = f"{ev.time_of_day_min // 60:02d}:{ev.time_of_day_min % 60:02d}"
            self._table.setItem(row, 0, QTableWidgetItem(time_str))
            self._table.setItem(row, 1, QTableWidgetItem(str(ev.duration_min)))
            self._table.setItem(row, 2, QTableWidgetItem(f"{ev.intensity_pct:g}"))

    def _add_event(self) -> None:
        time_of_day_min = self._time_edit.time().hour() * 60 + self._time_edit.time().minute()
        self._events.append(
            ExerciseEvent(
                time_of_day_min=time_of_day_min,
                duration_min=self._duration_spin.value(),
                intensity_pct=self._intensity_spin.value(),
            )
        )
        self._events.sort(key=lambda e: e.time_of_day_min)
        self._redraw_table()

    def _remove_selected(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        del self._events[row]
        self._redraw_table()

    def _save(self) -> bool:
        person = self._get_active_person()
        if person is None:
            QMessageBox.information(self, "Exercise Configuration", "No active person selected.")
            return False
        person.exercise_events = list(self._events)
        self._on_change()
        return True

    def _send_to_board(self) -> None:
        if not self._save():
            return
        session = self._target_bar.begin()
        if session is None:
            QMessageBox.warning(self, "Exercise Configuration", "No connected device selected.")
            return
        session.queue_write("exercise", protocol.encode_clear_exercise())
        for ev in self._events:
            session.queue_write("exercise", protocol.encode_exercise_event(ev))
        restart_board(session)
        await_send_confirmation(session, self._send_status)

    def _read_from_board(self) -> None:
        """Request the board's currently stored exercise event list and load it into the active person."""
        if self._get_active_person() is None:
            QMessageBox.information(self, "Exercise Configuration", "No active person selected.")
            return
        session = self._target_bar.begin()
        if session is None:
            QMessageBox.warning(self, "Exercise Configuration", "No connected device selected.")
            return
        if self._read_session is not None:
            try:
                self._read_session.config_read.disconnect(self._on_config_read)
            except TypeError:
                pass
        self._read_session = session
        session.config_read.connect(self._on_config_read)
        session.request_read("exercise_list")

    def _on_config_read(self, _address: str, char_key: str, data: bytes) -> None:
        """Load an Exercise Events Readback into the table (see person_config_window.py's
        _on_config_read for why this deliberately skips self._on_change())."""
        if char_key != "exercise_list":
            return
        self._events = protocol.decode_exercise_events(data)
        person = self._get_active_person()
        if person is not None:
            person.exercise_events = list(self._events)
        self._redraw_table()
