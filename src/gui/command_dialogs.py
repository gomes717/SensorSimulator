"""The small modal forms behind the Commands panel buttons: Food, Exercise, PISA.

Each asks for the numbers of one one-shot event and nothing else — which sensor
it goes to is decided by the page that opened the dialog, so there is no target
picker here.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QWidget

from gui.widgets import NoWheelDoubleSpinBox, NoWheelSpinBox


class _Dialog(QDialog):
    """A form plus OK / Cancel."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._form = QFormLayout(self)

    def _finish(self) -> None:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._form.addRow(buttons)


def _spin(lo: int, hi: int, value: int, suffix: str) -> NoWheelSpinBox:
    box = NoWheelSpinBox()
    box.setRange(lo, hi)
    box.setValue(value)
    box.setSuffix(suffix)
    return box


def _dspin(lo: float, hi: float, value: float, suffix: str) -> NoWheelDoubleSpinBox:
    box = NoWheelDoubleSpinBox()
    box.setRange(lo, hi)
    box.setValue(value)
    box.setSuffix(suffix)
    return box


class FoodDialog(_Dialog):
    """A one-shot carb bolus: quantity and how long to spread it over."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Send Food", parent)
        self._carbs = _dspin(0.1, 500.0, 50.0, " g")
        self._spread = _spin(1, 240, 15, " min")
        self._form.addRow("Carbs:", self._carbs)
        self._form.addRow("Spread over:", self._spread)
        self._finish()

    def values(self) -> tuple[float, int]:
        """(carbs_g, spread_min)."""
        return self._carbs.value(), self._spread.value()


class ExerciseDialog(_Dialog):
    """A one-shot exercise bout: duration and intensity."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Send Exercise", parent)
        self._duration = _spin(1, 300, 30, " min")
        self._intensity = _dspin(0.0, 100.0, 50.0, " %")
        self._form.addRow("Duration:", self._duration)
        self._form.addRow("Intensity:", self._intensity)
        self._finish()

    def values(self) -> tuple[int, float]:
        """(duration_min, intensity_pct)."""
        return self._duration.value(), self._intensity.value()


class PisaDialog(_Dialog):
    """A one-shot PISA event: duration and peak attenuation.

    PISA (Pressure-Induced Sensor Attenuation) is a transient downward
    attenuation of the sensor signal that does not reflect real glucose; the
    board and the local model scale the reading by
    ``1 - depth * sin(pi * elapsed / duration)`` while it is active.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Send PISA", parent)
        self._duration = _spin(1, 120, 10, " min")
        self._depth = _dspin(5.0, 90.0, 40.0, " %")
        self._form.addRow("Duration:", self._duration)
        self._form.addRow("Peak attenuation:", self._depth)
        self._finish()

    def values(self) -> tuple[int, float]:
        """(duration_min, depth_frac) with depth_frac in [0, 1]."""
        return self._duration.value(), self._depth.value() / 100.0
