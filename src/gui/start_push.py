"""Start's first step: put the app's profile for every model-backed sensor on the board.

The run is only worth comparing if the board and the local "expected" model start
from the same inputs. So Start does not trust whatever the board happens to hold:
it reads each live sensor's person (model, parameters) and sensor profile and its
meal / exercise schedules from the app, writes them to the board, and only then
starts the run. :meth:`AppState.board_plan` is the single list of what that means;
the local engines are built from the same list, so the two lines cannot disagree.

Each sensor is also told the name of the user it runs. A sensor that replays a CSV is left
alone — its recording is uploaded separately (by Send to…).

The writes go through ONE session, one slot after another: the sensor-select cursor
is a single value on the board shared by every connection, so two sessions pushing
at once would land each other's config on the wrong slot.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer

from api import protocol
from gui.app_state import AppState
from models import user_send
from models.types import PersonProfile, SensorProfile

# The board applies (and saves to flash) every write; a four-sensor push with
# schedules takes tens of seconds at worst. Past this the link is presumed gone.
_PUSH_TIMEOUT_MS = 90_000


def slot_entry(slot: int, person: PersonProfile, sensor: SensorProfile | None) -> dict:
    """One ``send_board_layout`` entry: the ordered writes that make *slot* run *person*.

    The schedules are cleared before they are written — the board keeps what an
    earlier session left, and a stale meal on the board is exactly how the two models
    drift apart. ``data_source`` is set to the model: sending parameters alone does
    not switch a slot off a CSV it replayed earlier. (The writes are shared with
    Send to…: :func:`models.user_send.model_writes`.)
    """
    return {"slot": slot, "writes": user_send.model_writes(person, sensor), "csv": None}


class StartPush(QObject):
    """Writes the app's per-sensor profiles to the board before a run starts."""

    def __init__(
        self,
        state: AppState,
        board,
        show_status: Callable[[str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._board = board
        self._show_status = show_status

    def run(self, on_done: Callable[[bool], None]) -> None:
        """Push the config, then call ``on_done(ok)``. Synchronous when there is
        nothing to push (Model Only, no live sensor, only CSV sensors); otherwise it
        returns at once and calls back when the board has taken every write."""
        live = [] if self._state.model_only else self._board.live_sessions()
        if not live:
            on_done(True)
            return
        speed = protocol.encode_speed(self._state.speed_mult)
        plan = self._state.board_plan(self._board.live_slots())
        if not plan:
            self._board.broadcast("speed", speed)
            on_done(True)
            return

        entries = [slot_entry(slot, person, sensor) for slot, (person, sensor) in plan.items()]
        # Tell each sensor who it runs — but only if the board lists the characteristic (a stale
        # Windows services cache can hide it right after a firmware update) and the name fits:
        # the model is what Start is for, and it must not fail over the name.
        if live[0].exposes("user_name"):
            for entry, (person, _sensor) in zip(entries, plan.values(), strict=True):
                name = user_send.name_write(person.name)
                if name is not None:
                    entry["writes"].insert(0, name)
        entries[0]["writes"].insert(0, ("speed", speed))
        names = ", ".join(str(slot + 1) for slot in plan)
        self._show_status(f"Sending the configuration to sensor(s) {names}…")
        self._push(live[0], entries, names, on_done)

    def _push(self, session, entries: list[dict], names: str, on_done) -> None:
        finished = {"done": False}

        def finish(ok: bool, message: str) -> None:
            if finished["done"]:
                return
            finished["done"] = True
            try:
                session.board_layout_finished.disconnect(on_finished)
            except TypeError:
                pass
            if ok:
                self._show_status(f"✓ Configuration sent to sensor(s) {names}.")
            else:
                self._show_status(
                    f"⚠ Could not send the configuration ({message}) — the run was not started."
                )
            on_done(ok)

        def on_finished(_address: str, ok: bool, message: str) -> None:
            finish(ok, message)

        session.board_layout_finished.connect(on_finished)
        QTimer.singleShot(_PUSH_TIMEOUT_MS, lambda: finish(False, "the board did not answer"))
        session.send_board_layout(entries, run=False)
