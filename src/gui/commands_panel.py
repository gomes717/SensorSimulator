"""The Commands panel: one-shot Food / Exercise / PISA events for ONE sensor.

A single row of three buttons; each opens a small modal form for that event's
numbers (``gui/command_dialogs.py``) and, once accepted, emits a typed signal.
Whoever hosts the panel decides which sensor the command goes to — each sensor
tab hosts its own, so a command can only ever reach the sensor whose tab it was
sent from.

While a command cannot be sent (no run in progress, no live sensor, …) the
buttons stay visible but disabled and the reason is shown beside them, instead of
opening a dialog that tells you after the fact.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from gui.command_dialogs import ExerciseDialog, FoodDialog, PisaDialog


class CommandsPanel(QWidget):
    """Food… / Exercise… / PISA… buttons and a one-line status."""

    food_requested = pyqtSignal(float, int)  # carbs_g, spread over (min)
    exercise_requested = pyqtSignal(int, float)  # duration_min, intensity_pct
    pisa_requested = pyqtSignal(int, float)  # duration_min, depth_frac in [0, 1]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._reason = ""
        self._food_btn = QPushButton("Food…")
        self._exercise_btn = QPushButton("Exercise…")
        self._pisa_btn = QPushButton("PISA…")
        self._food_btn.clicked.connect(self._ask_food)
        self._exercise_btn.clicked.connect(self._ask_exercise)
        self._pisa_btn.clicked.connect(self._ask_pisa)
        self._result = QLabel("")

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for button in self.send_buttons():
            row.addWidget(button)
        row.addWidget(self._result, 1)

    # -- the dialogs (also the seam a test can replace) ---------------------

    def _ask_food(self) -> None:
        dialog = FoodDialog(self)
        if dialog.exec() == FoodDialog.DialogCode.Accepted:
            self.food_requested.emit(*dialog.values())

    def _ask_exercise(self) -> None:
        dialog = ExerciseDialog(self)
        if dialog.exec() == ExerciseDialog.DialogCode.Accepted:
            self.exercise_requested.emit(*dialog.values())

    def _ask_pisa(self) -> None:
        dialog = PisaDialog(self)
        if dialog.exec() == PisaDialog.DialogCode.Accepted:
            self.pisa_requested.emit(*dialog.values())

    # -- sending without the dialog (scripts, tests) -------------------------

    def send_food(self, carbs_g: float, spread_min: int) -> bool:
        """Emit a Food command as the dialog would; False (nothing sent) if blocked."""
        if self._reason:
            return False
        self.food_requested.emit(carbs_g, spread_min)
        return True

    def send_exercise(self, duration_min: int, intensity_pct: float) -> bool:
        """Emit an Exercise command as the dialog would; False if blocked."""
        if self._reason:
            return False
        self.exercise_requested.emit(duration_min, intensity_pct)
        return True

    def send_pisa(self, duration_min: int, depth_pct: float) -> bool:
        """Emit a PISA command (depth as a percentage, like the field); False if blocked."""
        if self._reason:
            return False
        self.pisa_requested.emit(duration_min, depth_pct / 100.0)
        return True

    def send_buttons(self) -> tuple[QPushButton, QPushButton, QPushButton]:
        """The (food, exercise, pisa) buttons."""
        return self._food_btn, self._exercise_btn, self._pisa_btn

    # -- availability / feedback ----------------------------------------

    def set_blocked(self, reason: str) -> None:
        """Disable the buttons with *reason* shown beside them, or enable with ``""``."""
        previous, self._reason = self._reason, reason
        for button in self.send_buttons():
            button.setEnabled(not reason)
            button.setToolTip(reason)
        if reason:
            self._result.setText(reason)
        elif self._result.text() == previous:
            self._result.setText("")  # the "why it was blocked" text, not a real result

    @property
    def blocked_reason(self) -> str:
        """Why the panel is disabled right now (``""`` when it is usable)."""
        return self._reason

    def set_result(self, text: str) -> None:
        """Show the outcome of the last command ("✓ …" / "⚠ …")."""
        self._result.setText(text)

    def result_text(self) -> str:
        """The text currently on the status line."""
        return self._result.text()
