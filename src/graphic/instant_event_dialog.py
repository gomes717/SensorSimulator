"""Small dialogs for injecting a one-shot food/exercise event into an already-running
simulation — distinct from FoodConfigWindow/ExerciseConfigWindow, which edit the
recurring-daily schedule and (like every other config write) reset the run when sent."""
from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QSpinBox


class FoodInstantDialog(QDialog):
    """Prompts for a one-shot carb bolus: quantity and how long to spread it over."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert Food Now")
        layout = QFormLayout(self)

        self._carbs_spin = QDoubleSpinBox()
        self._carbs_spin.setRange(0.1, 500.0)
        self._carbs_spin.setValue(50.0)
        self._carbs_spin.setSuffix(" g")
        layout.addRow("Carbs:", self._carbs_spin)

        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(1, 240)
        self._duration_spin.setValue(15)
        self._duration_spin.setSuffix(" min")
        layout.addRow("Spread over:", self._duration_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> tuple[float, int]:
        """Returns (carbs_g, duration_min)."""
        return self._carbs_spin.value(), self._duration_spin.value()


class ExerciseInstantDialog(QDialog):
    """Prompts for a one-shot exercise bout: duration and intensity."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert Exercise Now")
        layout = QFormLayout(self)

        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(1, 300)
        self._duration_spin.setValue(30)
        self._duration_spin.setSuffix(" min")
        layout.addRow("Duration:", self._duration_spin)

        self._intensity_spin = QDoubleSpinBox()
        self._intensity_spin.setRange(0.0, 100.0)
        self._intensity_spin.setValue(50.0)
        self._intensity_spin.setSuffix(" %")
        layout.addRow("Intensity:", self._intensity_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> tuple[int, float]:
        """Returns (duration_min, intensity_pct)."""
        return self._duration_spin.value(), self._intensity_spin.value()


class PisaInstantDialog(QDialog):
    """Prompts for a one-shot PISA event: duration and peak attenuation.

    PISA (Pressure-Induced Sensor Attenuation) is a transient downward
    attenuation of the sensor signal — a *false low* that does not reflect real
    hypoglycaemia. The board/engine multiply the reading by
    ``1 - depth * sin(pi * elapsed/duration)`` while active.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert PISA Now")
        layout = QFormLayout(self)

        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(1, 120)
        self._duration_spin.setValue(10)
        self._duration_spin.setSuffix(" min")
        layout.addRow("Duration:", self._duration_spin)

        self._depth_spin = QDoubleSpinBox()
        self._depth_spin.setRange(5.0, 90.0)
        self._depth_spin.setValue(40.0)
        self._depth_spin.setSuffix(" %")
        layout.addRow("Peak attenuation:", self._depth_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> tuple[int, float]:
        """Returns (duration_min, depth_frac) with depth_frac in [0, 1]."""
        return self._duration_spin.value(), self._depth_spin.value() / 100.0
