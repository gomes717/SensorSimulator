"""The CSV page of the profile screen: the recorded 24 h window a user replays in CSV mode.

"Choose CSV file…" opens the CSV Analysis window as a picker (the file *and* the 24 h region are
chosen there, against the trace and its metrics); the window picked is **copied** into the user, so
the original file is not needed again. The page shows what the user holds and draws it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from PyQt6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from gui.csv_analysis_window import CsvAnalysisWindow
from gui.schedule_graph import ScheduleGraph
from gui.widgets import wrapped_label
from models import user_csv, user_edit, user_schedule
from models.types import User

_NONE_YET = "No CSV window chosen yet — choose a Dexcom file and the 24 h region to replay."
_BOARD_NO_DATA = (
    "This user was read from a board that is replaying a recording, but no recording could "
    "be read from it (nothing is committed on that sensor) — choose a file to give it one."
)


def open_csv_picker(on_pick: Callable[[str, datetime], None]) -> CsvAnalysisWindow:
    """Show the CSV Analysis window as a picker; *on_pick(path, start)* is called on confirm."""
    window = CsvAnalysisWindow(on_pick=on_pick)
    window.show()
    window.raise_()
    window.activateWindow()
    return window


class CsvPage(QWidget):
    """Shows and chooses the user's recorded window."""

    def __init__(
        self,
        user: User,
        changed: Callable[[], None],
        open_picker: Callable[[Callable[[str, datetime], None]], object] = open_csv_picker,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._user = user
        self._changed = changed
        self._open_picker = open_picker
        # Kept on self: a top-level window with no parent is garbage-collected (and vanishes)
        # the moment the last reference goes.
        self._picker: object | None = None

        layout = QVBoxLayout(self)
        self.choose_button = QPushButton("Choose CSV file…")
        self.choose_button.clicked.connect(self._choose)
        layout.addWidget(self.choose_button)
        self.info = wrapped_label("")
        layout.addWidget(self.info)
        self.message = wrapped_label("")
        self.message.setStyleSheet("color: #b00020;")
        layout.addWidget(self.message)
        self.graph = ScheduleGraph("Recorded glucose", "Glucose (mg/dL)", "#3f8fd0", filled=False)
        layout.addWidget(self.graph, 1)
        self.refresh()

    # -- user -> widgets ------------------------------------------------------------

    def refresh(self) -> None:
        """Describe and draw the window the user holds."""
        track = self._user.csv
        if track is None:
            self.info.setText(_BOARD_NO_DATA if self._user.mode == "csv" else _NONE_YET)
            self.graph.set_values([0.0] * 1440)
            return
        lines = [f"Window from {track.source_name or 'a recording'} — {len(track.samples)} samples"]
        if track.start_iso:
            try:
                begin = datetime.fromisoformat(track.start_iso)
            except ValueError:
                lines.append(f"starts {track.start_iso}")
            else:
                end = begin + timedelta(seconds=len(track.samples) * track.interval_s)
                lines.append(f"{begin:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M}")
        meals = len(track.foodlog)
        lines.append(f"{meals} meal{'s' if meals != 1 else ''} in its food log (report only)")
        self.info.setText("\n".join(lines))
        self.graph.set_values(user_schedule.csv_series(track))

    # -- widgets -> user ------------------------------------------------------------

    def _choose(self) -> None:
        self._picker = self._open_picker(self._apply_picked)

    def _apply_picked(self, path: str, start: datetime) -> None:
        """The region picked in CSV Analysis becomes this user's window."""
        try:
            track = user_csv.load_track(path, start)
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        self.message.setText("")
        user_edit.set_csv(self._user, track)
        self.refresh()
        self._changed()
