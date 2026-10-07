"""Helpers for pushing configuration to a board: restart it, and confirm it applied."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QLabel

from api import ble_uuids, protocol
from services.ble_session import BleSession

SEND_CONFIRMATION_TIMEOUT_MS = 3000


def restart_board(session: BleSession) -> None:
    """Tell *session*'s board to (re)start ticking now — call after any config push.

    The firmware already reinitializes model/sensor state and resets its
    simulation clock on every config write it receives (person, sensor,
    food/exercise event) — but it only resumes *advancing* that state while
    its run state is RUNNING. Without this, a board left paused/stopped from
    an earlier Stop/Pause sits frozen on the freshly-applied config instead
    of visibly restarting, which is what "send config" should do.
    """
    session.queue_write("run_state", protocol.encode_run_state(protocol.RUN_STATE_RUNNING))


def await_send_confirmation(
    session: BleSession, status_label: QLabel, on_confirmed: Callable[[], None] | None = None
) -> None:
    """Show live feedback that a just-sent config write actually reached and was
    applied by the board, instead of leaving the user unsure whether it worked.

    *on_confirmed* runs only if the board actually acknowledges — it is how
    callers commit anything that should describe the board's real state (the
    slot -> patient rename), rather than what was merely put on the wire.

    Reuses the board's reset_sync notification (see ble_uuids.RESET_SYNC_UUID)
    — it fires the instant ANY config write takes effect, so "the next one to
    arrive after I sent this" is a reliable (if not perfectly attributed, when
    multiple writes are in flight) confirmation signal. Falls back to a
    timeout warning if nothing arrives, e.g. because the connection dropped.
    """
    if not session.notifies(ble_uuids.RESET_SYNC_UUID):
        # A congested 3rd/4th link can subscribe some characteristics and not
        # others. Waiting on a channel this session never got would always time
        # out and read as a dead connection, which is the wrong thing to go
        # debug — the write itself was queued normally.
        status_label.setText("Sent — this link has no confirmation channel (not re-subscribed)")
        return
    status_label.setText("Sending to board…")
    state = {"done": False}

    def on_sync(_address: str) -> None:
        if state["done"]:
            return
        state["done"] = True
        try:
            session.reset_sync.disconnect(on_sync)
        except TypeError:
            pass
        status_label.setText("✓ Applied on board")
        if on_confirmed is not None:
            on_confirmed()

    def on_timeout() -> None:
        if state["done"]:
            return
        state["done"] = True
        try:
            session.reset_sync.disconnect(on_sync)
        except TypeError:
            pass
        status_label.setText("⚠ No confirmation from board (check connection)")

    session.reset_sync.connect(on_sync)
    QTimer.singleShot(SEND_CONFIRMATION_TIMEOUT_MS, on_timeout)
