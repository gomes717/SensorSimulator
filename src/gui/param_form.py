"""A form of one spin box per model parameter, in the board's order — shared by the glucose-model
and the sensor-noise sections of the profile screen.

Some parameters are not edited here (the weight is the model's ``BW`` and is set on the Profile
page): those rows show their value as text and say where to change it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QFormLayout, QLabel, QWidget

from gui.widgets import NoWheelDoubleSpinBox


class ParamForm(QWidget):
    """Edit named float parameters; :attr:`edited` carries ``(name, value)``."""

    edited = pyqtSignal(str, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._form = QFormLayout(self)
        self._form.setContentsMargins(0, 0, 0, 0)
        self.spins: dict[str, NoWheelDoubleSpinBox] = {}
        self.locked: dict[str, QLabel] = {}

    def set_params(
        self,
        names: Sequence[str],
        values: Mapping[str, float],
        locked: Mapping[str, str] | None = None,
    ) -> None:
        """Rebuild the form for *names* with *values*. A name in *locked* is shown read-only,
        its text being ``locked[name]`` (formatted with ``{value}``)."""
        while self._form.rowCount():
            self._form.removeRow(0)
        self.spins.clear()
        self.locked.clear()
        for name in names:
            value = values.get(name, 0.0)
            if locked and name in locked:
                label = QLabel(locked[name].format(value=f"{value:g}"))
                self._form.addRow(f"{name}:", label)
                self.locked[name] = label
                continue
            spin = NoWheelDoubleSpinBox()
            spin.setDecimals(6)
            spin.setRange(-1_000_000.0, 1_000_000.0)
            spin.setValue(value)
            spin.valueChanged.connect(lambda v, n=name: self.edited.emit(n, v))
            self._form.addRow(f"{name}:", spin)
            self.spins[name] = spin

    def sync(self, values: Mapping[str, float], locked: Mapping[str, str] | None = None) -> None:
        """Show *values* in the existing rows without emitting :attr:`edited` (and without
        touching a spin box that already shows the value, so typing is not disturbed)."""
        for name, spin in self.spins.items():
            value = values.get(name, 0.0)
            if spin.value() != value:
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        for name, label in self.locked.items():
            if locked and name in locked:
                label.setText(locked[name].format(value=f"{values.get(name, 0.0):g}"))
