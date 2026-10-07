"""The one timeline every sensor graph shares.

The x-axis is *simulated* seconds since the run's t=0: one wall-clock second at
speed x60 is 60 sim-seconds. Start re-anchors it for every graph at once, which
is what keeps one sensor's received line, its expected line and every other
sensor's graph on the same origin — and why each graph reads this instead of
keeping its own copy.
"""

from __future__ import annotations

from datetime import datetime


class RunClock:
    """Run start time plus the speed multiplier that scales wall time to sim time."""

    def __init__(self, t0: datetime, speed_mult: float = 1.0) -> None:
        self.t0 = t0
        self.speed_mult = max(1.0, float(speed_mult))

    def anchor(self, now: datetime) -> None:
        """Make *now* the run's t=0."""
        self.t0 = now

    def set_speed(self, speed_mult: float) -> None:
        """New sim-time scale for the next points (the caller re-anchors + clears)."""
        self.speed_mult = max(1.0, float(speed_mult))

    def elapsed_seconds(self, timestamp_str: str) -> float:
        """ISO timestamp (BLE message or engine tick) -> simulated seconds since t0."""
        wall = (datetime.fromisoformat(timestamp_str) - self.t0).total_seconds()
        return wall * self.speed_mult
