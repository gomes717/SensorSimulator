"""Fault-injection panel — a registry of sensor faults and a small window to fire them.

Only PISA (compression low) is wired for now; the registry + `MainWindow.inject_fault`
entry point are the scaffold for adding signal dropout, pressure spike, stuck sensor,
etc. later (each would gain a firmware slot like `instant_pisa[]` and a `FAULTS` entry).
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from graphic.instant_event_dialog import PisaInstantDialog

# kind -> {label, desc, dialog, kind}. `MainWindow.inject_fault(kind, values)` does the work.
FAULTS: dict[str, dict] = {
    "pisa": {
        "label": "Compression low (PISA)",
        "desc": "Pressure-Induced Sensor Attenuation — a transient downward "
                "attenuation of the sensor signal (a false low), no real "
                "hypoglycaemia. The affected interval is shaded on the graph.",
        "dialog": PisaInstantDialog,
    },
}


class FaultPanel(QWidget):
    """Lists the registered faults; each row opens its dialog and calls
    ``main.inject_fault(kind, values)``."""

    def __init__(self, main, parent=None) -> None:
        super().__init__(parent)
        self._main = main
        self.setWindowTitle("Faults")
        self.resize(460, 260)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Inject a sensor fault into the running simulation. Faults do not "
            "reset the run and are mirrored to a connected board."
        ))
        for kind, spec in FAULTS.items():
            layout.addWidget(self._fault_group(kind, spec))
        layout.addStretch(1)

    def _fault_group(self, kind: str, spec: dict) -> QGroupBox:
        group = QGroupBox(spec["label"])
        box = QVBoxLayout(group)
        desc = QLabel(spec["desc"])
        desc.setWordWrap(True)
        box.addWidget(desc)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("Inject…")
        btn.clicked.connect(lambda _=False, k=kind, s=spec: self._inject(k, s))
        row.addWidget(btn)
        box.addLayout(row)
        return group

    def _inject(self, kind: str, spec: dict) -> None:
        dialog = spec["dialog"](self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._main.inject_fault(kind, dialog.values())
