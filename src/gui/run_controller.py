"""The run state machine behind the toolbar's Start/Pause/Resume and Stop buttons.

Pulled out of :class:`MainWindow`, where the state flag, the button labels, the
board run-state broadcast and its status-bar report were spread over six methods.
This owns *when* a run starts, pauses, resumes and stops and tells the board; the
window supplies what a restart means for its graphs and engines through
:class:`RunHooks`.

Start is two steps: first the app's profile for every model-backed sensor (person
parameters, sensor, meal and exercise schedules) is written to the board, and only
once the board has it does the run begin — so the board and the local expected model
start from the same inputs. The button reads "Starting…" in between.

The two buttons are **blocked until there is something to run**: at least one
sensor whose link is live, or Model Only (which has no board). A run already in
progress keeps them usable so it can always be paused or stopped, even if every
sensor has dropped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QPushButton

from api import protocol

STOPPED = "stopped"
STARTING = "starting"
RUNNING = "running"
PAUSED = "paused"

_LABELS = {STOPPED: "Start", STARTING: "Starting…", RUNNING: "Pause", PAUSED: "Resume"}
_RUN_STATE_NAMES = {
    protocol.RUN_STATE_STOPPED: "Stop",
    protocol.RUN_STATE_RUNNING: "Start",
    protocol.RUN_STATE_PAUSED: "Pause",
}
# How often the buttons re-check for a live sensor. A link can drop or come up
# without anything here being told, so this polls the sessions' own live flag.
_REFRESH_MS = 500


class _Board(Protocol):
    """The slice of BoardLink the run controller uses."""

    def live_sessions(self) -> list: ...

    def sessions(self) -> dict: ...

    def broadcast(self, char_key: str, payload: bytes) -> int: ...


class _Engines(Protocol):
    """The slice of EnginePool the run controller uses."""

    def pause_all(self) -> None: ...

    def resume_all(self) -> None: ...


@dataclass
class RunHooks:
    """What starting / stopping a run means to the rest of the app."""

    board: _Board
    engines: _Engines
    model_only: Callable[[], bool]
    restart_engine: Callable[[], None]  # stop pool + reset graphs + rebuild paused pool
    stop_engine: Callable[[], None]
    reset_graph_view: Callable[[], None]
    anchor_clock: Callable[[], None]  # re-anchor the graphs' t=0 to "now"
    # Write the app's per-sensor profiles to the board, then call back with whether it
    # worked. Calls back at once when there is nothing to write (Model Only, no board).
    push_config: Callable[[Callable[[bool], None]], None]
    show_status: Callable[[str], None]


class RunController(QObject):
    """Owns the run state and the Start/Pause/Resume + Stop buttons."""

    # Emitted on every re-evaluation (a timer tick or a state change), so whoever
    # shows per-sensor state that depends on live links can re-check it too.
    refreshed = pyqtSignal()

    def __init__(
        self, hooks: RunHooks, start_pause_btn: QPushButton, stop_btn: QPushButton, parent=None
    ) -> None:
        super().__init__(parent)
        self._hooks = hooks
        self._start_pause_btn = start_pause_btn
        self._stop_btn = stop_btn
        self._state = STOPPED
        self._locked = False
        self._start_id = 0  # which Start the pending push belongs to (Stop cancels it)
        start_pause_btn.clicked.connect(self.toggle)
        stop_btn.clicked.connect(self.stop)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_enabled)
        self._refresh_timer.start(_REFRESH_MS)
        self._relabel()
        self.refresh_enabled()

    @property
    def start_pause_btn(self) -> QPushButton:
        """The toolbar's Start / Pause / Resume button."""
        return self._start_pause_btn

    @property
    def stop_btn(self) -> QPushButton:
        """The toolbar's Stop button."""
        return self._stop_btn

    @property
    def state(self) -> str:
        """``"stopped"``, ``"starting"``, ``"running"`` or ``"paused"``."""
        return self._state

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def can_control(self) -> bool:
        """Whether there is anything for Start/Stop to act on right now."""
        if self._locked or self._state == STARTING:
            return False
        h = self._hooks
        return self._state != STOPPED or h.model_only() or bool(h.board.live_sessions())

    def refresh_enabled(self) -> None:
        """Re-evaluate the buttons' enabled state (also driven by a timer)."""
        enabled = self.can_control()
        self._start_pause_btn.setEnabled(enabled)
        self._stop_btn.setEnabled(enabled)
        self.refreshed.emit()

    def commands_block_reason(self) -> str:
        """Why Food/Exercise/PISA cannot be sent right now, or ``""`` if they can.

        They need a run in progress and a sensor to receive them (a live link,
        or Model Only's local model)."""
        h = self._hooks
        if self._locked:
            return "Commands are locked while CGMS-only mode is on."
        if not (h.model_only() or h.board.live_sessions()):
            return "No live sensor — connect one to send commands."
        if self._state == PAUSED:
            return "The run is paused — resume it to send commands."
        if self._state == STARTING:
            return "The run is starting — wait for the configuration to reach the sensors."
        if self._state != RUNNING:
            return "Start a run to send commands."
        return ""

    def set_locked(self, locked: bool) -> None:
        """CGMS-only mode locks every control that would send a rejected write."""
        self._locked = locked
        self.refresh_enabled()

    def _relabel(self) -> None:
        self._start_pause_btn.setText(_LABELS[self._state])

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def toggle(self) -> None:
        """Start (from stopped), pause (from running), or resume (from paused)."""
        if self._state == STARTING:
            return
        if self._state == STOPPED:
            self.start()
            return
        if self._state == RUNNING:
            self._hooks.engines.pause_all()
            self._state = PAUSED
            self._send(protocol.RUN_STATE_PAUSED)
        else:
            self._hooks.engines.resume_all()
            self._state = RUNNING
            self._send(protocol.RUN_STATE_RUNNING)
        self._relabel()
        self.refresh_enabled()

    def start(self) -> None:
        """Start a fresh run: write the app's profiles to every connected board, then
        reset+resume the local engine and reset+start the boards, both anchored to the
        moment the board has its configuration."""
        if self._state != STOPPED:
            return
        self._state = STARTING
        self._start_id += 1
        start_id = self._start_id
        self._relabel()
        self.refresh_enabled()
        self._hooks.push_config(lambda ok: self._begin(start_id, ok))

    def _begin(self, start_id: int, pushed: bool) -> None:
        """The board has (or failed to take) its configuration: begin the run."""
        if start_id != self._start_id or self._state != STARTING:
            return  # stopped while the configuration was on its way
        h = self._hooks
        if not pushed:
            self._state = STOPPED
            self._relabel()
            self.refresh_enabled()
            return
        h.restart_engine()  # preps graphs + a paused pool (not RUNNING yet, so paused)
        h.anchor_clock()
        h.engines.resume_all()
        self._state = RUNNING
        self._relabel()
        self._send(protocol.RUN_STATE_STOPPED)
        self._send(protocol.RUN_STATE_RUNNING)
        self.refresh_enabled()

    def stop(self) -> None:
        """Stop and discard the local engine, clear the graphs, and reset the board."""
        self._start_id += 1  # a configuration still on its way must not start the run
        self._hooks.stop_engine()
        self._state = STOPPED
        self._hooks.reset_graph_view()
        self._send(protocol.RUN_STATE_STOPPED)
        self._relabel()
        self.refresh_enabled()

    def reset_to_stopped(self) -> None:
        """Forget the run without telling the board (a mode change that stops it elsewhere)."""
        self._start_id += 1
        self._state = STOPPED
        self._relabel()
        self.refresh_enabled()

    # ------------------------------------------------------------------
    # Board
    # ------------------------------------------------------------------

    def _send(self, value: int) -> None:
        """Broadcast a run-state byte to every connected board (see PROTOCOL_SPEC.md).

        Reports the outcome: a board that never receives RUNNING stays idle and
        pushes nothing, which otherwise looks like "the sensor sends no data"
        with nothing anywhere saying the command went nowhere.
        """
        board = self._hooks.board
        reached = board.broadcast("run_state", protocol.encode_run_state(value))
        connected = len(board.sessions())
        if not connected:
            return
        name = _RUN_STATE_NAMES.get(value, "Run state")
        if reached:
            self._hooks.show_status(f"✓ {name} sent to {reached} of {connected} sensor(s).")
        else:
            self._hooks.show_status(
                f"⚠ {name} reached 0 of {connected} connected sensor(s) — their links are "
                "down. Reconnect in the Bluetooth window."
            )
