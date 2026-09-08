"""Scenario window — load a JSON scenario and run its timed actions."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models import scenario as scenario_mod

_SCENARIO_DIR = Path(__file__).resolve().parent.parent.parent / "scenarios"


class ScenarioWindow(QWidget):
    """Pick a `.json` scenario, Run / Stop it, watch the step log.

    *main* provides ``_scenario_dispatch(kind, args) -> str``.
    """

    def __init__(self, main, parent=None) -> None:
        super().__init__(parent)
        self._main = main
        self._runner: scenario_mod.ScenarioRunner | None = None
        self.setWindowTitle("Scenario")
        self.resize(560, 420)

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        open_btn = QPushButton("Open scenario…")
        open_btn.clicked.connect(self._open)
        top.addWidget(open_btn)
        self._file_label = QLabel("No scenario loaded")
        top.addWidget(self._file_label, 1)
        layout.addLayout(top)

        controls = QHBoxLayout()
        self._run_btn = QPushButton("Run")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._run)
        controls.addWidget(self._run_btn)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        controls.addWidget(self._stop_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        layout.addWidget(QLabel("Steps:"))
        self._log = QListWidget()
        layout.addWidget(self._log, 1)

        self._name = ""
        self._actions: list = []

    def _open(self) -> None:
        start_dir = str(_SCENARIO_DIR) if _SCENARIO_DIR.is_dir() else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open scenario", start_dir, "Scenario JSON (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            self._name, self._actions = scenario_mod.load(path)
        except (ValueError, OSError, KeyError) as exc:
            QMessageBox.warning(self, "Scenario", f"Could not load scenario:\n{exc}")
            return
        self._file_label.setText(f"{self._name}  ({len(self._actions)} actions)")
        self._run_btn.setEnabled(True)
        self._log.clear()

    def _run(self) -> None:
        if not self._actions:
            return
        self._stop()
        self._log.clear()
        self._log.addItem(f"▶ {self._name}")
        self._runner = scenario_mod.ScenarioRunner(
            self._actions, self._main._scenario_dispatch, self
        )
        self._runner.step.connect(self._log.addItem)
        self._runner.finished.connect(self._on_finished)
        self._runner.start()
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

    def _stop(self) -> None:
        if self._runner is not None:
            self._runner.stop()
            self._runner = None
        self._run_btn.setEnabled(bool(self._actions))
        self._stop_btn.setEnabled(False)

    def _on_finished(self) -> None:
        self._log.addItem("■ done")
        self._stop()

    def closeEvent(self, event) -> None:
        self._stop()
        super().closeEvent(event)
