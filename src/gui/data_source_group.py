"""The per-patient "Data source" chooser — physiological model vs a recorded CSV
region — plus the "Send CSV to Board" upload.

Lives in the Person Configuration window (issue 16), right next to the model it
replaces. Kept as its own widget so the Person window stays a thin host: it
passes in how to reach the selected profile, the persist callback, and the
target BLE session.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from api import protocol
from gui.device_target import restart_board
from models import food_log_csv
from models.engine import load_csv_window
from models.types import PersonProfile


class DataSourceGroup(QGroupBox):
    def __init__(
        self,
        current_person: Callable[[], PersonProfile | None],
        on_change: Callable[[], None],
        target_session: Callable[[], object | None],
        parent=None,
    ) -> None:
        super().__init__("Data source", parent)
        self._current_person = current_person
        self._on_change = on_change
        self._target_session = target_session
        self._upload_session = None

        box = QVBoxLayout(self)
        self.model_radio = QRadioButton("Physiological model (parameters below)")
        self.csv_radio = QRadioButton("CSV region (replay a recorded 24 h window)")
        self.model_radio.toggled.connect(self._save)
        box.addWidget(self.model_radio)
        box.addWidget(self.csv_radio)

        self._path_label = QLabel("—")
        self._path_label.setWordWrap(True)
        box.addWidget(self._path_label)
        hint = QLabel(
            "Pick the CSV file and 24 h window in the CSV Analysis window, then "
            "assign it to this patient there."
        )
        hint.setWordWrap(True)
        hint.setEnabled(False)
        box.addWidget(hint)

        send_row = QHBoxLayout()
        self._send_btn = QPushButton("Send CSV to Board")
        self._send_btn.clicked.connect(self.send_csv)
        send_row.addWidget(self._send_btn)
        self._send_status = QLabel("")
        self._send_status.setWordWrap(True)
        send_row.addWidget(self._send_status, 1)
        box.addLayout(send_row)

        self._note = QLabel(
            "This patient replays the recorded CSV window — the physiological "
            "model and its parameters below are not used. Switch back to the "
            "model above to edit them."
        )
        self._note.setWordWrap(True)
        self._note.setVisible(False)
        box.addWidget(self._note)

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-sync the radios + CSV info from the currently selected profile."""
        person = self._current_person()
        for w in (self.model_radio, self.csv_radio):
            w.setEnabled(person is not None)
        if person is None:
            self._send_btn.setEnabled(False)
            self._path_label.setText("—")
            return
        is_csv = getattr(person, "data_source", "model") == "csv"
        for w in (self.model_radio, self.csv_radio):
            w.blockSignals(True)
        self.csv_radio.setChecked(is_csv)
        self.model_radio.setChecked(not is_csv)
        for w in (self.model_radio, self.csv_radio):
            w.blockSignals(False)
        self._note.setVisible(is_csv)

        lines = [person.csv_path or "— no CSV region assigned —"]
        if getattr(person, "csv_window_start_iso", None):
            lines.append(f"window start: {person.csv_window_start_iso}")
        food_log = getattr(person, "food_log_path", None)
        if not food_log and person.csv_path:
            food_log = food_log_csv.matching_food_log_path(person.csv_path)
        if food_log:
            lines.append(f"food log (auto): {food_log}")
        self._path_label.setText("\n".join(lines))
        self._send_btn.setEnabled(is_csv and bool(person.csv_path))

    def _save(self) -> None:
        person = self._current_person()
        if person is None:
            return
        person.data_source = "csv" if self.csv_radio.isChecked() else "model"
        self._on_change()  # persists + re-applies the model-form lock + refreshes us

    def send_csv(self) -> None:
        """Build the glucose + food-log tracks for the selected CSV patient, upload
        them to the target board, then switch the board to CSV playback."""
        person = self._current_person()
        if person is None or getattr(person, "data_source", "model") != "csv":
            QMessageBox.information(
                self, "Send CSV", "Set this patient's data source to CSV first."
            )
            return
        samples, interval_s, foodlog = load_csv_window(person)
        if not samples:
            QMessageBox.warning(
                self, "Send CSV", "Could not build the 24 h window — re-assign it in CSV Analysis."
            )
            return
        session = self._target_session()
        if session is None:
            QMessageBox.warning(self, "Send CSV", "No connected board selected.")
            return

        uploads = protocol.build_csv_uploads(
            samples, interval_s, foodlog, person.csv_window_start_iso
        )
        try:
            session.csv_upload_progress.disconnect(self._on_progress)
            session.csv_upload_finished.disconnect(self._on_finished)
        except TypeError:
            pass
        session.csv_upload_progress.connect(self._on_progress)
        session.csv_upload_finished.connect(self._on_finished)
        self._send_status.setText("Uploading CSV…")
        self._send_btn.setEnabled(False)
        session.start_csv_upload(uploads)
        self._upload_session = session

    def _on_progress(self, _address: str, sent: int, total: int) -> None:
        self._send_status.setText(f"Uploading CSV… {sent}/{total} B")

    def _on_finished(self, _address: str, ok: bool, message: str) -> None:
        self._send_btn.setEnabled(True)
        if ok:
            if self._upload_session is not None:
                self._upload_session.queue_write("data_source", protocol.encode_data_source(True))
                restart_board(self._upload_session)
            self._send_status.setText(f"✓ {message} — board set to CSV playback")
        else:
            self._send_status.setText(f"⚠ Upload failed: {message}")
