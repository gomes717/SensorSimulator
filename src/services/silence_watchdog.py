"""Detect a BLE link that is connected and "subscribed" but delivers nothing.

On a multi-sensor board the Windows BLE stack can accept ``start_notify`` for the
CGM Measurement characteristic and then never deliver a notification, while the
link itself stays up (measured on hardware: the 4th sensor, with the firmware's
notification stagger off). Nothing in the connection state shows it, so the app
sat on a healthy-looking row that never drew a point.

This class is the policy only — no Bluetooth, no clock of its own — so it can be
tested exactly. The session feeds it three things (measurement notifications,
run-state changes, the subscription time) and asks :meth:`poll` what to do:
re-arm the subscription (stop/start notify on the same connection), and once a
few re-arms in a row have not helped, tell the user.
"""

from __future__ import annotations

import enum
import threading


class Action(enum.Enum):
    """What the session should do after a :meth:`SilenceWatchdog.poll`."""

    NONE = "none"
    REARM = "rearm"  # stop/start notify on the measurement characteristic
    REPORT = "report"  # re-arm AND tell the user the link has gone silent


class SilenceWatchdog:
    """Silence clock for one session's CGM Measurement subscription.

    Notifications arrive every ``limit_s / 4`` or so, so a stretch of ``limit_s``
    with none means the subscription is not delivering. While the board is not
    running there is nothing to deliver, so the clock does not run then.

    Called from two threads (the session's event loop feeds data and polls; the
    GUI thread flips the run state), hence the lock.
    """

    def __init__(self, limit_s: float, report_after: int = 3) -> None:
        self._limit_s = limit_s
        self._report_after = report_after
        self._lock = threading.Lock()
        self._armed = False
        self._running = True
        self._since = 0.0  # when the current silence started counting
        self._rearms = 0  # consecutive re-arms that produced no data
        self._reported = False

    def arm(self, now: float) -> None:
        """The measurement characteristic has just been subscribed."""
        with self._lock:
            self._armed = True
            self._since = now

    def restart_clock(self, now: float) -> None:
        """Give the link a fresh grace period (the board just reset or resumed)."""
        with self._lock:
            self._since = now

    def set_running(self, running: bool, now: float) -> None:
        """The board's run state changed. A stopped board sends nothing, by design."""
        with self._lock:
            if running and not self._running:
                self._since = now
            self._running = running

    def on_data(self, now: float) -> bool:
        """A CGM Measurement arrived. Returns True if this ends a reported silence."""
        with self._lock:
            recovered = self._reported
            self._since = now
            self._rearms = 0
            self._reported = False
            return recovered

    def poll(self, now: float) -> Action:
        """Decide what to do now; call it often (it is cheap)."""
        with self._lock:
            if not self._armed or not self._running or now - self._since < self._limit_s:
                return Action.NONE
            # One verdict per silent stretch: count it, and wait another full
            # limit before the next one rather than re-arming every tick.
            self._since = now
            self._rearms += 1
            if self._rearms >= self._report_after and not self._reported:
                self._reported = True
                return Action.REPORT
            return Action.REARM
