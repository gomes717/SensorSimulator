"""Timed-action scenario runner: load a JSON list of actions and fire them on a
wall-clock timeline.

Scenario file shape (see scenarios/README.md):

    {
      "name": "PISA demo",
      "actions": [
        {"at_s": 0,  "kind": "speed",        "args": {"multiplier": 60}},
        {"at_s": 1,  "kind": "run_state",    "args": {"state": "start"}},
        {"at_s": 10, "kind": "insert_food",  "args": {"carbs_g": 60, "duration_min": 15}},
        {"at_s": 40, "kind": "inject_fault", "args": {"fault": "pisa",
                                                      "duration_min": 10, "depth_frac": 0.4}},
        {"at_s": 90, "kind": "run_state",    "args": {"state": "stop"}}
      ]
    }

`at_s` is seconds from when the scenario starts (wall clock — under a high speed
multiplier the simulation compresses, the schedule does not).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


@dataclass
class Action:
    at_s: float
    kind: str
    args: dict


def load(path: str | Path) -> tuple[str, list[Action]]:
    """Parse a scenario JSON file into ``(name, [Action, ...])`` sorted by time."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    name = str(data.get("name") or Path(path).stem)
    actions = [
        Action(at_s=float(a["at_s"]), kind=str(a["kind"]), args=dict(a.get("args", {})))
        for a in data.get("actions", [])
    ]
    actions.sort(key=lambda a: a.at_s)
    return name, actions


class ScenarioRunner(QObject):
    """Fires each Action via *dispatch* at its scheduled offset.

    *dispatch(kind, args) -> str* performs the action and returns a short human
    description for the step log. Runs on the GUI thread (QTimer), so *dispatch*
    can touch widgets directly.
    """

    step = pyqtSignal(str)  # human-readable description of an executed action
    finished = pyqtSignal()

    def __init__(
        self, actions: list[Action], dispatch: Callable[[str, dict], str], parent=None
    ) -> None:
        super().__init__(parent)
        self._actions = actions
        self._dispatch = dispatch
        self._timers: list[QTimer] = []

    def start(self) -> None:
        self.stop()
        if not self._actions:
            self.finished.emit()
            return
        last = self._actions[-1].at_s
        for action in self._actions:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda a=action: self._fire(a))
            timer.start(max(0, int(action.at_s * 1000)))
            self._timers.append(timer)
        end = QTimer(self)
        end.setSingleShot(True)
        end.timeout.connect(self.finished.emit)
        end.start(max(0, int(last * 1000)) + 250)
        self._timers.append(end)

    def stop(self) -> None:
        for timer in self._timers:
            timer.stop()
        self._timers.clear()

    def _fire(self, action: Action) -> None:
        try:
            desc = self._dispatch(action.kind, action.args)
        except Exception as exc:  # pylint: disable=broad-except
            desc = f"{action.kind} FAILED: {exc}"
        self.step.emit(f"t+{action.at_s:>5.0f}s  {desc}")
