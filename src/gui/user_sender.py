"""Sending one user to one sensor and reporting how it went.

A send is one ``send_board_layout`` push through the chosen session: the board's Sensor-select
cursor is set to that sensor's slot, the user's writes (see :mod:`models.user_send`) follow, a CSV
user's recording is uploaded, and the board is left running. Only one send runs at a time — the
cursor is one value shared by every connection.

Checked before anything is sent, so a send that cannot work fails with a reason instead of half
applying: a user the board cannot take (no name, no CSV window), a sensor whose link is down, and a
sensor that does not list the user-name characteristic — which, right after a firmware update, is
Windows showing a cached copy of the board's services (see ``docs/E2E_TEST_PLAN.md``).
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer

from models import user_send
from models.types import User

# A CSV upload is a few hundred bytes of chunks behind per-write pacing; past this the link is
# presumed gone.
_DEFAULT_TIMEOUT_MS = 120_000

DoneCallback = Callable[[bool, str], None]


def slot_of(session) -> int:
    """The slot *session*'s sensor is (a single-sensor board has none: slot 0)."""
    return session.slot_index if session.slot_index is not None else 0


class UserSender(QObject):
    """Pushes a user to a sensor; the result goes to the callback given to :meth:`send`."""

    def __init__(self, timeout_ms: int = _DEFAULT_TIMEOUT_MS, parent: QObject | None = None):
        super().__init__(parent)
        self._timeout_ms = timeout_ms
        self._on_done: DoneCallback | None = None
        self._cleanup: Callable[[], None] | None = None
        self._generation = 0

    @property
    def busy(self) -> bool:
        """True while a send is waiting for the board."""
        return self._on_done is not None

    def send(self, session, user: User, on_done: DoneCallback) -> bool:
        """Send *user* to *session*'s sensor. False (and *on_done* never called) if a send is
        already running; otherwise True and *on_done(ok, message)* is called exactly once."""
        if self.busy:
            return False
        slot = slot_of(session)
        problem = user_send.refusal(user)
        if problem is None and not session.is_live:
            problem = "The sensor is not connected."
        if problem is None and not session.exposes("user_name"):
            problem = (
                "This sensor does not list 'user_name'. If its firmware was just updated, Windows "
                "may still be showing the old list of services — disconnect and reconnect the "
                "sensor, then send again."
            )
        if problem is not None:
            on_done(False, problem)
            return True
        entry = user_send.slot_entry(slot, user)
        label = f'"{user.name.strip()}" to sensor {slot + 1}'
        self._on_done = on_done
        self._generation += 1
        token = self._generation

        def on_finished(_address: str, ok: bool, message: str) -> None:
            self._finish(ok, f"Sent {label}." if ok else f"Could not send: {message}")

        session.board_layout_finished.connect(on_finished)
        self._cleanup = lambda: session.board_layout_finished.disconnect(on_finished)
        QTimer.singleShot(self._timeout_ms, lambda: self._timed_out(token))
        session.send_board_layout([entry], run=True)
        return True

    def _timed_out(self, token: int) -> None:
        if token == self._generation and self.busy:
            self._finish(False, "Could not send: the board did not answer in time.")

    def _finish(self, ok: bool, message: str) -> None:
        on_done, self._on_done = self._on_done, None
        cleanup, self._cleanup = self._cleanup, None
        if cleanup is not None:
            try:
                cleanup()
            except (TypeError, ValueError):  # already disconnected
                pass
        if on_done is not None:
            on_done(ok, message)
