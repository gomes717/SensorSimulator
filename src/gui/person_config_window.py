"""Window for creating/editing simulated-patient (person) profiles and sending them to a board."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
from gui.bluetooth_window import BluetoothWindow
from gui.data_source_group import DataSourceGroup
from gui.device_target import DeviceTargetBar, await_send_confirmation, restart_board
from gui.widgets import NoWheelDoubleSpinBox
from models import board_layout, cambridge, deichmann, royparker, uva_padova
from models.types import MODEL_LABELS, ModelId, PersonProfile


@dataclass(frozen=True)
class _Hooks:
    """The three callbacks this window reports back to the app through."""

    changed: Callable[[], None]
    slot_assigned: Callable[..., None]
    selected: Callable[[PersonProfile | None], None]


_MODEL_MODULES = {
    ModelId.CAMBRIDGE: cambridge,
    ModelId.UVA_PADOVA: uva_padova,
    ModelId.ROYPARKER: royparker,
    ModelId.DEICHMANN: deichmann,
}


class PersonConfigWindow(QWidget):
    """Manage saved PersonProfiles: pick a model, edit its parameters, save, or send to a board.

    *profiles* is the list instance owned by MainWindow — mutated in place so
    every other window (the bottom-bar combo, Food/Exercise windows) sees the
    same objects. *on_change* is called after every save/add/delete so the
    caller can persist to disk and refresh its own UI.
    """

    def __init__(
        self,
        profiles: list[PersonProfile],
        on_change: Callable[[], None],
        get_bluetooth_window: Callable[[], BluetoothWindow],
        on_slot_assigned: Callable[..., None] | None = None,
        on_selected: Callable[[PersonProfile | None], None] | None = None,
        parent=None,
    ) -> None:
        """Build the profile list, parameter form, and Save/Send controls."""
        super().__init__(parent)
        self.setWindowTitle("Person Configuration")
        self.resize(640, 520)

        self._profiles = profiles
        # How this window reports back to the app. Grouped so the three
        # callbacks travel together: *changed* persists profiles, *slot_assigned*
        # records which slot a send pushed to (there is no Board Layout screen
        # any more), and *selected* makes the highlighted row the active patient
        # (the Configuration window's Person combo is gone).
        self._hooks = _Hooks(
            changed=on_change,
            slot_assigned=on_slot_assigned or (lambda *_a, **_k: None),
            selected=on_selected or (lambda _p: None),
        )
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
        self._model_combo = QComboBox()
        for model_id in ModelId:
            self._model_combo.addItem(MODEL_LABELS[model_id], model_id)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        form_top.addRow("Model:", self._model_combo)
        right_layout.addLayout(form_top)

        self._ds = DataSourceGroup(
            self._current_person, self._hooks.changed, lambda: self._target_bar.begin()
        )
        right_layout.addWidget(self._ds)

        self._params_group = QGroupBox("Model parameters")
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
        # Greyed out with the model form for a CSV-backed patient. "Send to
        # Board" is NOT in here: it stays live and uploads the CSV instead.
        self._model_buttons = (read_btn,)
        right_layout.addLayout(buttons)

        self._send_status = QLabel("")
        right_layout.addWidget(self._send_status)

        splitter.addWidget(right)
        splitter.setSizes([200, 440])
        layout.addWidget(splitter, 1)

        self._reload_list()

    # ------------------------------------------------------------------
    # List management
    # ------------------------------------------------------------------

    def _reload_list(self) -> None:
        """Repopulate the profile list from self._profiles, keeping the selection if possible."""
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
        """Create a new profile with default Cambridge parameters and select it."""
        name, ok = QInputDialog.getText(self, "New Person", "Name:")
        if not ok or not name.strip():
            return
        profile = PersonProfile(
            name=name.strip(), model_id=ModelId.CAMBRIDGE, params=cambridge.default_params()
        )
        self._profiles.append(profile)
        self._hooks.changed()
        self._reload_list()
        self._list.setCurrentRow(len(self._profiles) - 1)

    def _delete_profile(self) -> None:
        """Delete the currently selected profile, if any."""
        if self._current_index is None:
            return
        del self._profiles[self._current_index]
        self._hooks.changed()
        self._current_index = None
        self._reload_list()

    def _on_row_selected(self, row: int) -> None:
        """Load the selected profile's fields into the form."""
        if row < 0 or row >= len(self._profiles):
            self._current_index = None
            self._clear_form()
            return
        self._current_index = row
        profile = self._profiles[row]
        self._name_edit.setText(profile.name)
        self._model_combo.blockSignals(True)
        self._model_combo.setCurrentIndex(list(ModelId).index(profile.model_id))
        self._model_combo.blockSignals(False)
        self._rebuild_param_form(profile.model_id, profile.params)
        self._apply_data_source_lock(profile)
        self._ds.refresh()
        self._hooks.selected(profile)

    def reload(self) -> None:
        """Re-read the current profile (e.g. after its data source / CSV window
        changed in CSV Analysis) so the form + data-source group stay in sync.
        Public for MainWindow to call."""
        self._reload_list()
        if self._current_index is not None:
            self._apply_data_source_lock(self._profiles[self._current_index])
        self._ds.refresh()

    def _apply_data_source_lock(self, profile: PersonProfile | None) -> None:
        """Grey out the physiological-model editor when *profile* is CSV-backed —
        the CSV is played back verbatim, so its model parameters are unused."""
        is_csv = profile is not None and getattr(profile, "data_source", "model") == "csv"
        tip = (
            "Disabled: this patient replays a recorded CSV window. The "
            "physiological model and its parameters are not used for a "
            "CSV-backed patient — switch the data source back to the model "
            "above to edit these."
            if is_csv
            else ""
        )
        for w in (self._model_combo, self._params_group, *self._model_buttons):
            w.setEnabled(not is_csv)
            w.setToolTip(tip)

    def _current_person(self):
        if self._current_index is None:
            return None
        return self._profiles[self._current_index]

    # ------------------------------------------------------------------
    # Parameter form
    # ------------------------------------------------------------------

    def _clear_form(self) -> None:
        self._name_edit.clear()
        self._rebuild_param_form(ModelId.CAMBRIDGE, {})
        self._apply_data_source_lock(None)
        self._ds.refresh()

    def _on_model_changed(self) -> None:
        """Rebuild the parameter form with the newly selected model's defaults."""
        model_id = self._model_combo.currentData()
        module = _MODEL_MODULES[model_id]
        self._rebuild_param_form(model_id, module.default_params())

    def _rebuild_param_form(self, model_id: ModelId, values: dict[str, float]) -> None:
        """Replace the parameter form with one spinbox per field of *model_id*, in order."""
        while self._params_form.rowCount():
            self._params_form.removeRow(0)
        self._param_spinboxes.clear()

        module = _MODEL_MODULES[model_id]
        defaults = module.default_params()
        for name in module.PARAM_NAMES:
            spin = NoWheelDoubleSpinBox()
            spin.setDecimals(6)
            spin.setRange(-1_000_000.0, 1_000_000.0)
            spin.setValue(values.get(name, defaults[name]))
            self._params_form.addRow(f"{name}:", spin)
            self._param_spinboxes[name] = spin

    # ------------------------------------------------------------------
    # Save / send
    # ------------------------------------------------------------------

    def _save_current(self) -> None:
        """Write the form's values back into the selected profile and persist."""
        if self._current_index is None:
            QMessageBox.information(self, "Person Configuration", "Select or add a profile first.")
            return
        profile = self._profiles[self._current_index]
        profile.name = self._name_edit.text().strip() or profile.name
        profile.model_id = self._model_combo.currentData()
        profile.params = {name: spin.value() for name, spin in self._param_spinboxes.items()}
        self._hooks.changed()
        self._reload_list()

    def _send_to_board(self) -> None:
        """Send this patient to the target slot: their recorded CSV window if that
        is their data source, otherwise their model + parameters.

        One button either way — what gets sent follows the Data source choice
        above rather than making the user pick the matching button."""
        if self._current_index is None:
            QMessageBox.information(self, "Person Configuration", "Select or add a profile first.")
            return
        self._save_current()
        profile = self._profiles[self._current_index]
        # Captured now, not when the board answers: the target combo can move
        # while the write is in flight, and the rename belongs to the slot this
        # send actually went to.
        slot = self._target_bar.selected_slot()
        name = profile.name
        if getattr(profile, "data_source", "model") == "csv":
            self._ds.send_csv(on_applied=lambda: self._hooks.slot_assigned(slot, person=name))
            return
        session = self._target_bar.begin()
        if session is None:
            QMessageBox.warning(self, "Person Configuration", "No connected device selected.")
            return
        payload = protocol.encode_person_config(profile.model_id, profile.params)
        session.queue_write("person", payload)
        restart_board(session)
        await_send_confirmation(
            session,
            self._send_status,
            on_confirmed=lambda: self._hooks.slot_assigned(slot, person=name),
        )

    def _read_from_board(self) -> None:
        """Request the board's currently applied person config and load it into the selected profile."""
        if self._current_index is None:
            QMessageBox.information(self, "Person Configuration", "Select or add a profile first.")
            return
        session = self._target_bar.begin()
        if session is None:
            QMessageBox.warning(self, "Person Configuration", "No connected device selected.")
            return
        # The board answers for the slot the target identity owns, so land the
        # readback on the profile the board layout assigns to that slot.
        # Otherwise "Read from Board" on Sensor 3 quietly loads its values into
        # whichever row happened to be selected in the list.
        slot = self._target_bar.selected_slot()
        assigned = (
            board_layout.person_for(board_layout.advert_name(slot)) if slot is not None else None
        )
        if assigned:
            self._select_profile_named(assigned)
        if self._read_session is not None:
            try:
                self._read_session.config_read.disconnect(self._on_config_read)
            except TypeError:
                pass
        self._read_session = session
        session.config_read.connect(self._on_config_read)
        session.request_read("person")
        where = f" (Sensor {slot + 1})" if slot is not None else ""
        self._send_status.setText(
            f"Reading{where} into “{self._profiles[self._current_index].name}”…"
        )

    def _select_profile_named(self, name: str) -> bool:
        """Select the profile row called *name*; False when no such profile exists."""
        index = next((i for i, p in enumerate(self._profiles) if p.name == name), None)
        if index is None:
            return False
        self._list.setCurrentRow(index)
        return True

    def _on_config_read(self, _address: str, char_key: str, data: bytes) -> None:
        """Load a Person Config readback into the form for inspection/editing.

        Deliberately does NOT call self._hooks.changed() — that persists to disk
        and restarts the live comparison engine/graphs, which would make a
        passive "what's on the board right now" read visibly disrupt an
        in-progress run. Updates the in-memory profile (so the form reflects
        the board and a later Save persists it) without either side effect;
        click Save explicitly to keep it.
        """
        if char_key != "person" or self._current_index is None:
            return
        decoded = protocol.decode_person_config(data)
        if decoded is None:
            QMessageBox.warning(
                self, "Person Configuration", "Could not decode the board's response."
            )
            return
        model_id, params = decoded
        profile = self._profiles[self._current_index]
        profile.model_id = model_id
        profile.params = params
        self._on_row_selected(self._current_index)
        self._send_status.setText(
            f"✓ Loaded the board's config into “{profile.name}” (not saved yet)"
        )
