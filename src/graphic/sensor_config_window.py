"""Window for creating/editing CGM sensor (noise model) profiles and sending them to a board."""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from api import protocol
from graphic.bluetooth_window import BluetoothWindow
from graphic.device_target import DeviceTargetBar, await_send_confirmation, restart_board
from models import sensors
from models.types import SensorId, SensorProfile

_SENSOR_LABELS = {
    SensorId.IDEAL: "Ideal CGM (no noise)",
    SensorId.BRETON: "Breton & Kovatchev 2008",
    SensorId.FACCHINETTI: "Facchinetti et al. 2014",
}
_SENSOR_DEFAULTS = {
    SensorId.IDEAL: sensors.ideal_default_params,
    SensorId.BRETON: sensors.breton_default_params,
    SensorId.FACCHINETTI: sensors.facchinetti_default_params,
}


class SensorConfigWindow(QWidget):
    """Manage saved SensorProfiles: pick a noise model, edit its parameters, save, or send."""

    def __init__(
        self,
        profiles: list[SensorProfile],
        on_change: Callable[[], None],
        get_bluetooth_window: Callable[[], BluetoothWindow],
        parent=None,
    ) -> None:
        """Build the profile list, parameter form, and Save/Send controls."""
        super().__init__(parent)
        self.setWindowTitle("Sensor Configuration")
        self.resize(560, 480)

        self._profiles = profiles
        self._on_change = on_change
        self._param_spinboxes: dict[str, QDoubleSpinBox] = {}
        self._current_index: int | None = None
        self._read_session = None  # tracks which BleSession config_read is currently connected to

        layout = QVBoxLayout(self)
        self._target_bar = DeviceTargetBar(get_bluetooth_window)
        layout.addWidget(self._target_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_row_selected)
        left_layout.addWidget(self._list)
        list_buttons = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_profile)
        list_buttons.addWidget(add_btn)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete_profile)
        list_buttons.addWidget(delete_btn)
        left_layout.addLayout(list_buttons)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        form_top = QFormLayout()
        self._name_edit = QLineEdit()
        form_top.addRow("Name:", self._name_edit)
        self._sensor_combo = QComboBox()
        for sensor_id in SensorId:
            self._sensor_combo.addItem(_SENSOR_LABELS[sensor_id], sensor_id)
        self._sensor_combo.currentIndexChanged.connect(self._on_sensor_changed)
        form_top.addRow("Sensor:", self._sensor_combo)
        right_layout.addLayout(form_top)

        self._params_group = QGroupBox("Sensor parameters")
        self._params_form = QFormLayout(self._params_group)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._params_group)
        right_layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_current)
        buttons.addWidget(save_btn)
        send_btn = QPushButton("Send to Board")
        send_btn.clicked.connect(self._send_to_board)
        buttons.addWidget(send_btn)
        read_btn = QPushButton("Read from Board")
        read_btn.clicked.connect(self._read_from_board)
        buttons.addWidget(read_btn)
        right_layout.addLayout(buttons)

        self._send_status = QLabel("")
        right_layout.addWidget(self._send_status)

        splitter.addWidget(right)
        splitter.setSizes([200, 360])
        layout.addWidget(splitter, 1)

        self._reload_list()

    # ------------------------------------------------------------------
    # List management
    # ------------------------------------------------------------------

    def _reload_list(self) -> None:
        previous = self._current_index
        self._list.blockSignals(True)
        self._list.clear()
        for profile in self._profiles:
            self._list.addItem(profile.name)
        self._list.blockSignals(False)
        if self._profiles:
            row = previous if previous is not None and previous < len(self._profiles) else 0
            self._list.setCurrentRow(row)
        else:
            self._current_index = None
            self._clear_form()

    def _add_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New Sensor", "Name:")
        if not ok or not name.strip():
            return
        profile = SensorProfile(
            name=name.strip(), sensor_id=SensorId.BRETON, params=sensors.breton_default_params()
        )
        self._profiles.append(profile)
        self._on_change()
        self._reload_list()
        self._list.setCurrentRow(len(self._profiles) - 1)

    def _delete_profile(self) -> None:
        if self._current_index is None:
            return
        del self._profiles[self._current_index]
        self._on_change()
        self._current_index = None
        self._reload_list()

    def _on_row_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._profiles):
            self._current_index = None
            self._clear_form()
            return
        self._current_index = row
        profile = self._profiles[row]
        self._name_edit.setText(profile.name)
        self._sensor_combo.blockSignals(True)
        self._sensor_combo.setCurrentIndex(list(SensorId).index(profile.sensor_id))
        self._sensor_combo.blockSignals(False)
        self._rebuild_param_form(profile.sensor_id, profile.params)

    # ------------------------------------------------------------------
    # Parameter form
    # ------------------------------------------------------------------

    def _clear_form(self) -> None:
        self._name_edit.clear()
        self._rebuild_param_form(SensorId.BRETON, {})

    def _on_sensor_changed(self) -> None:
        sensor_id = self._sensor_combo.currentData()
        self._rebuild_param_form(sensor_id, _SENSOR_DEFAULTS[sensor_id]())

    def _rebuild_param_form(self, sensor_id: SensorId, values: dict[str, float]) -> None:
        while self._params_form.rowCount():
            self._params_form.removeRow(0)
        self._param_spinboxes.clear()

        defaults = _SENSOR_DEFAULTS[sensor_id]()
        for name in protocol.sensor_param_names(sensor_id):
            spin = QDoubleSpinBox()
            spin.setDecimals(6)
            spin.setRange(-1_000_000.0, 1_000_000.0)
            spin.setValue(values.get(name, defaults[name]))
            self._params_form.addRow(f"{name}:", spin)
            self._param_spinboxes[name] = spin

    # ------------------------------------------------------------------
    # Save / send
    # ------------------------------------------------------------------

    def _save_current(self) -> None:
        if self._current_index is None:
            QMessageBox.information(self, "Sensor Configuration", "Select or add a profile first.")
            return
        profile = self._profiles[self._current_index]
        profile.name = self._name_edit.text().strip() or profile.name
        profile.sensor_id = self._sensor_combo.currentData()
        profile.params = {name: spin.value() for name, spin in self._param_spinboxes.items()}
        self._on_change()
        self._reload_list()

    def _send_to_board(self) -> None:
        if self._current_index is None:
            QMessageBox.information(self, "Sensor Configuration", "Select or add a profile first.")
            return
        self._save_current()
        profile = self._profiles[self._current_index]
        session = self._target_bar.begin()
        if session is None:
            QMessageBox.warning(self, "Sensor Configuration", "No connected device selected.")
            return
        payload = protocol.encode_sensor_config(profile.sensor_id, profile.params)
        session.queue_write("sensor", payload)
        restart_board(session)
        await_send_confirmation(session, self._send_status)

    def _read_from_board(self) -> None:
        """Request the board's currently applied sensor config and load it into the selected profile."""
        if self._current_index is None:
            QMessageBox.information(self, "Sensor Configuration", "Select or add a profile first.")
            return
        session = self._target_bar.begin()
        if session is None:
            QMessageBox.warning(self, "Sensor Configuration", "No connected device selected.")
            return
        if self._read_session is not None:
            try:
                self._read_session.config_read.disconnect(self._on_config_read)
            except TypeError:
                pass
        self._read_session = session
        session.config_read.connect(self._on_config_read)
        session.request_read("sensor")

    def _on_config_read(self, _address: str, char_key: str, data: bytes) -> None:
        """Load a Sensor Config readback into the form (see person_config_window.py's
        _on_config_read for why this deliberately skips self._on_change())."""
        if char_key != "sensor" or self._current_index is None:
            return
        decoded = protocol.decode_sensor_config(data)
        if decoded is None:
            QMessageBox.warning(self, "Sensor Configuration", "Could not decode the board's response.")
            return
        sensor_id, params = decoded
        profile = self._profiles[self._current_index]
        profile.sensor_id = sensor_id
        profile.params = params
        self._on_row_selected(self._current_index)
