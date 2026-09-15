"""The per-patient "Data source" chooser — physiological model vs a recorded CSV
region — and the CSV upload behind Person Configuration's "Send to Board".

Lives in the Person Configuration window (issue 16), right next to the model it
replaces. Kept as its own widget so the Person window stays a thin host: it
passes in how to reach the selected profile, the persist callback, and the
target BLE session.

Which 24 h region a patient replays is picked in the CSV Analysis window, opened
from "Choose CSV file…" — this group only shows what was picked.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from PyQt6.QtWidgets import (
    QGroupBox,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from api import protocol
from gui.csv_analysis_window import CsvAnalysisWindow
from gui.device_target import restart_board
from gui.widgets import wrapped_label
from models import food_log_csv
from models.engine import load_csv_window
from models.types import PersonProfile

_WINDOW_HOURS = 24


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
        self._on_applied: Callable[[], None] | None = None
        self._picker: CsvAnalysisWindow | None = None

        box = QVBoxLayout(self)
        self.model_radio = QRadioButton("Physiological model (parameters below)")
        self.csv_radio = QRadioButton("CSV region (replay a recorded 24 h window)")
        self.model_radio.toggled.connect(self._save)
        box.addWidget(self.model_radio)
        box.addWidget(self.csv_radio)

        self._choose_btn = QPushButton("Choose CSV file…")
        self._choose_btn.clicked.connect(self._choose_csv)
        box.addWidget(self._choose_btn)

        self._path_label = wrapped_label("—")
        box.addWidget(self._path_label)

        box.addWidget(
            wrapped_label(
                "“Choose CSV file…” opens CSV Analysis: pick the file and the 24 h "
                "region there, against the trace and its metrics.",
                muted=True,
            )
        )

        self._send_status = wrapped_label("")
        box.addWidget(self._send_status)

        self._note = wrapped_label(
            "This patient replays the recorded CSV window — the physiological "
            "model and its parameters below are not used. Switch back to the "
            "model above to edit them."
        )
        self._note.setVisible(False)
        box.addWidget(self._note)

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-sync the radios + CSV info from the currently selected profile."""
        person = self._current_person()
        for w in (self.model_radio, self.csv_radio):
            w.setEnabled(person is not None)
        if person is None:
            self._choose_btn.setEnabled(False)
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
        self._choose_btn.setEnabled(is_csv)

        lines = [person.csv_path or "— no CSV file chosen —"]
        start = getattr(person, "csv_window_start_iso", None)
        if start:
            try:
                begin = datetime.fromisoformat(start)
            except ValueError:
                lines.append(f"window: {start}")
            else:
                end = begin + timedelta(hours=_WINDOW_HOURS)
                lines.append(f"window: {begin:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M}")
        food_log = getattr(person, "food_log_path", None)
        if not food_log and person.csv_path:
            food_log = food_log_csv.matching_food_log_path(person.csv_path)
        if food_log:
            lines.append(f"food log (auto): {food_log}")
        self._path_label.setText("\n".join(lines))

    def _save(self) -> None:
        person = self._current_person()
        if person is None:
            return
        person.data_source = "csv" if self.csv_radio.isChecked() else "model"
        self._on_change()  # persists + re-applies the model-form lock + refreshes us

    def _choose_csv(self) -> None:
        """Open the CSV Analysis window as a picker: the patient's file *and* the
        24 h region it replays are chosen there, against the trace and its
        metrics, and confirmed with "Use this 24 h window"."""
        if self._current_person() is None:
            return
        # Kept on self: a top-level QWidget with no parent is garbage-collected
        # (and vanishes) the moment the last reference goes out of scope.
        self._picker = CsvAnalysisWindow(on_pick=self._apply_picked_csv)
        self._picker.show()
        self._picker.raise_()
        self._picker.activateWindow()

    def _apply_picked_csv(self, path: str, start: datetime) -> None:
        """Make the region picked in CSV Analysis this patient's data source."""
        person = self._current_person()
        if person is None:
            return
        person.csv_path = path
        person.food_log_path = None  # auto-matched from the glucose CSV's id
        person.csv_window_start_iso = start.isoformat()
        person.data_source = "csv"
        self._on_change()

    def send_csv(self, on_applied: Callable[[], None] | None = None) -> None:
        """Build the glucose + food-log tracks for the selected CSV patient, upload
        them to the target board, then switch the board to CSV playback.

        *on_applied* runs only once the board has taken the whole upload, so a
        caller can commit state that should describe the board (the slot ->
        patient rename) rather than the attempt.
        """
        self._on_applied = on_applied
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
        session.start_csv_upload(uploads)
        self._upload_session = session

    def _on_progress(self, _address: str, sent: int, total: int) -> None:
        self._send_status.setText(f"Uploading CSV… {sent}/{total} B")

    def _on_finished(self, _address: str, ok: bool, message: str) -> None:
        if not ok:
            self._send_status.setText(f"⚠ Upload failed: {message}")
            return
        if self._upload_session is not None:
            self._upload_session.queue_write("data_source", protocol.encode_data_source(True))
            restart_board(self._upload_session)
        self._send_status.setText(f"✓ {message} — board set to CSV playback")
        if self._on_applied is not None:
            self._on_applied()
