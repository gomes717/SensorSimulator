"""Small dialogs for injecting a one-shot food/exercise event into an already-running
simulation — distinct from FoodConfigWindow/ExerciseConfigWindow, which edit the
recurring-daily schedule and (like every other config write) reset the run when sent."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
)

from gui.widgets import NoWheelDoubleSpinBox, NoWheelSpinBox


class _InstantDialog(QDialog):
    """Base: a QFormLayout, an optional 'Slot:' row for a multi-sensor board, and
    the standard OK/Cancel buttons. Subclasses add their own spin rows before
    calling _finish()."""

    def __init__(
        self,
        title: str,
        slots: int = 1,
        parent=None,
        *,
        slot_choices: list[tuple[int | None, str]] | None = None,
        default_slot: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._layout = QFormLayout(self)
        self._slot_combo: QComboBox | None = None
        # Prefer named choices (value, label) — "All sensors" + one per assigned
        # slot — over raw slot numbers (issue 04). Fall back to numbers if only
        # a count is given.
        if not slot_choices and slots and slots > 1:
            slot_choices = [(i, f"Sensor {i + 1}") for i in range(slots)]
        if slot_choices and len(slot_choices) > 1:
            self._slot_combo = QComboBox()
            for value, label in slot_choices:
                self._slot_combo.addItem(label, value)
            # Default to the sensor the user is looking at, not "All sensors":
            # inserting an event into every slot at once is rarely what is meant
            # while one row is selected.
            index = self._slot_combo.findData(default_slot)
            if index >= 0:
                self._slot_combo.setCurrentIndex(index)
            self._layout.addRow("Target:", self._slot_combo)

    def _finish(self) -> None:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._layout.addRow(buttons)

    def selected_slot(self) -> int | None:
        """The chosen sensor slot on a multi-sensor board, else None (all slots /
        single sensor — the caller broadcasts as before)."""
        return self._slot_combo.currentData() if self._slot_combo is not None else None


class FoodInstantDialog(_InstantDialog):
    """Prompts for a one-shot carb bolus: quantity and how long to spread it over."""

    def __init__(
        self,
        parent=None,
        slots: int = 1,
        *,
        slot_choices: list[tuple[int | None, str]] | None = None,
        default_slot: int | None = None,
    ) -> None:
        super().__init__(
            "Insert Food Now", slots, parent, slot_choices=slot_choices, default_slot=default_slot
        )

        self._carbs_spin = NoWheelDoubleSpinBox()
        self._carbs_spin.setRange(0.1, 500.0)
        self._carbs_spin.setValue(50.0)
        self._carbs_spin.setSuffix(" g")
        self._layout.addRow("Carbs:", self._carbs_spin)

        self._duration_spin = NoWheelSpinBox()
        self._duration_spin.setRange(1, 240)
        self._duration_spin.setValue(15)
        self._duration_spin.setSuffix(" min")
        self._layout.addRow("Spread over:", self._duration_spin)
        self._finish()

    def values(self) -> tuple[float, int]:
        """Returns (carbs_g, duration_min)."""
        return self._carbs_spin.value(), self._duration_spin.value()


class ExerciseInstantDialog(_InstantDialog):
    """Prompts for a one-shot exercise bout: duration and intensity."""

    def __init__(
        self,
        parent=None,
        slots: int = 1,
        *,
        slot_choices: list[tuple[int | None, str]] | None = None,
        default_slot: int | None = None,
    ) -> None:
        super().__init__(
            "Insert Exercise Now",
            slots,
            parent,
            slot_choices=slot_choices,
            default_slot=default_slot,
        )

        self._duration_spin = NoWheelSpinBox()
        self._duration_spin.setRange(1, 300)
        self._duration_spin.setValue(30)
        self._duration_spin.setSuffix(" min")
        self._layout.addRow("Duration:", self._duration_spin)

        self._intensity_spin = NoWheelDoubleSpinBox()
        self._intensity_spin.setRange(0.0, 100.0)
        self._intensity_spin.setValue(50.0)
        self._intensity_spin.setSuffix(" %")
        self._layout.addRow("Intensity:", self._intensity_spin)
        self._finish()

    def values(self) -> tuple[int, float]:
        """Returns (duration_min, intensity_pct)."""
        return self._duration_spin.value(), self._intensity_spin.value()


class PisaInstantDialog(_InstantDialog):
    """Prompts for a one-shot PISA event: duration and peak attenuation.

    PISA (Pressure-Induced Sensor Attenuation) is a transient downward
    attenuation of the sensor signal — a *false low* that does not reflect real
    hypoglycaemia. The board/engine multiply the reading by
    ``1 - depth * sin(pi * elapsed/duration)`` while active.
    """

    def __init__(
        self,
        parent=None,
        slots: int = 1,
        *,
        slot_choices: list[tuple[int | None, str]] | None = None,
        default_slot: int | None = None,
    ) -> None:
        super().__init__(
            "Insert PISA Now", slots, parent, slot_choices=slot_choices, default_slot=default_slot
        )

        self._duration_spin = NoWheelSpinBox()
        self._duration_spin.setRange(1, 120)
        self._duration_spin.setValue(10)
        self._duration_spin.setSuffix(" min")
        self._layout.addRow("Duration:", self._duration_spin)

        self._depth_spin = NoWheelDoubleSpinBox()
        self._depth_spin.setRange(5.0, 90.0)
        self._depth_spin.setValue(40.0)
        self._depth_spin.setSuffix(" %")
        self._layout.addRow("Peak attenuation:", self._depth_spin)
        self._finish()

    def values(self) -> tuple[int, float]:
        """Returns (duration_min, depth_frac) with depth_frac in [0, 1]."""
        return self._duration_spin.value(), self._depth_spin.value() / 100.0
