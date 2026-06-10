"""Background thread that loads Dexcom CSV files and replays readings one by one."""
from __future__ import annotations

import csv
import threading
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

DATASET_DIR = Path(__file__).parent.parent / "dataset"


def _load_dexcom(path: Path) -> list[dict]:
    """Parse a single Dexcom CSV file and return EGV rows as message dicts.

    Each dict contains: user_id, dev_id, glucose_value, timestamp.
    Rows with a missing timestamp or non-numeric glucose value are skipped.
    """
    user_id = path.stem.split("_")[1]  # "Dexcom_013" -> "013"
    readings: list[dict] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Event Type", "").strip() != "EGV":
                continue
            timestamp = row.get("Timestamp (YYYY-MM-DDThh:mm:ss)", "").strip()
            glucose_str = row.get("Glucose Value (mg/dL)", "").strip()
            if not timestamp or not glucose_str:
                continue
            try:
                glucose = float(glucose_str)
            except ValueError:
                continue
            readings.append({
                "user_id": user_id,
                "dev_id": row.get("Source Device ID", "").strip(),
                "glucose_value": glucose,
                "timestamp": timestamp,
            })
    return readings


class DataThread(QThread):
    """Worker thread that replays Dexcom readings in round-robin order.

    Emits ``new_message`` every 100 ms with the next reading for the current
    user.  When a user's data is exhausted the cursor wraps back to the first
    reading so playback loops indefinitely.

    All public query methods are thread-safe.
    """

    new_message = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        """Load all Dexcom CSV files found in the dataset directory."""
        super().__init__(parent)
        self._lock = threading.Lock()

        self._readings: dict[str, list[dict]] = {}
        self._indices: dict[str, int] = {}
        self._delivered: dict[str, list[dict]] = {}
        self._all_messages: list[dict] = []
        self._users: list[str] = []

        for path in sorted(DATASET_DIR.glob("Dexcom_*.csv")):
            rows = _load_dexcom(path)
            if not rows:
                continue
            uid = rows[0]["user_id"]
            self._readings[uid] = rows
            self._indices[uid] = 0
            self._delivered[uid] = []
            self._users.append(uid)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_users(self) -> list[str]:
        """Return the list of user IDs discovered from the dataset."""
        return list(self._users)

    def get_last_glucose(self, user_id: str) -> float | None:
        """Return the most recently delivered glucose value for *user_id*, or None."""
        with self._lock:
            entries = self._delivered.get(user_id, [])
            return entries[-1]["glucose_value"] if entries else None

    def get_user_data(self, user_id: str) -> list[dict]:
        """Return a snapshot of all readings delivered so far for *user_id*."""
        with self._lock:
            return list(self._delivered.get(user_id, []))

    def get_messages(self) -> list[dict]:
        """Return a snapshot of every message delivered so far, in emission order."""
        with self._lock:
            return list(self._all_messages)

    # ------------------------------------------------------------------
    # Thread loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Emit readings in round-robin order until interruption is requested."""
        cursor = 0
        while not self.isInterruptionRequested():
            if not self._users:
                self.msleep(100)
                continue

            user_id = self._users[cursor % len(self._users)]
            cursor += 1

            idx = self._indices[user_id]
            readings = self._readings[user_id]

            if idx >= len(readings):
                self._indices[user_id] = 0
                idx = 0

            msg = readings[idx]
            self._indices[user_id] = idx + 1
            with self._lock:
                self._delivered[user_id].append(msg)
                self._all_messages.append(msg)
            self.new_message.emit(msg)

            self.msleep(100)
