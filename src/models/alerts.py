"""Where a glucose reading sits against the clinical thresholds.

One definition shared by everything that colours or flags a reading — the graph's
red/yellow/green line, and a sensor tab's warning icon and critical blink — so
they can never disagree about the same number.

Levels, with the default thresholds (``app_settings``)::

    normal    70 <= g <= 180
    warning   54 <= g < 70      or   180 < g <= 250     (level-1: tbr1 / tar1)
    critical        g < 54      or         g > 250      (level-2: tbr2 / tar2)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Level(Enum):
    """How far out of range a reading is."""

    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Alert:
    """A reading's level and which way it is out of range ("low" / "high" / None)."""

    level: Level
    direction: str | None = None

    @property
    def is_normal(self) -> bool:
        """True when nothing needs flagging."""
        return self.level is Level.NORMAL

    def describe(self, glucose: float) -> str:
        """A sentence for a tooltip: "262 mg/dL — critically high"."""
        if self.is_normal:
            return f"{glucose:.0f} mg/dL — in range"
        adverb = "critically " if self.level is Level.CRITICAL else ""
        return f"{glucose:.0f} mg/dL — {adverb}{self.direction}"


NORMAL = Alert(Level.NORMAL)


def alert_for(glucose: float, thresholds: dict) -> Alert:
    """Classify *glucose* (mg/dL) against *thresholds* (the app_settings dict)."""
    if glucose < thresholds["tbr2_below"]:
        return Alert(Level.CRITICAL, "low")
    if glucose > thresholds["tar2_above"]:
        return Alert(Level.CRITICAL, "high")
    if glucose < thresholds["tbr1_below"]:
        return Alert(Level.WARNING, "low")
    if glucose > thresholds["tar1_above"]:
        return Alert(Level.WARNING, "high")
    return NORMAL
