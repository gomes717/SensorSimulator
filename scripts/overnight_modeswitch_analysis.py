"""Scoring for the overnight mode-switch test (``overnight_modeswitch.py``): pure functions, no BLE.

The board has two modes: Multi (standard BLE, three CGMS sensors) and Dexcom (a G6 transmitter plus
the app's one sensor). The test flips between them for hours and, after every flip, looks at what
the radio and the board's console say. The questions answered here are all about *what a correct
board shows*:

* which names it advertises in each mode (:func:`classify_adverts`);
* which mode switches it logged, in which order (:func:`parse_mode_lines`, :func:`score_switches`);
* whether the three slots' stored configuration survived the trip (:func:`fingerprint_diff`);
* how fast it came back (:func:`timing_summary`).
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field

MULTI, DEXCOM = "multi", "dexcom"
SENSOR_PREFIX = "Nordic Glucose Sensor"
APP_NAME = f"{SENSOR_PREFIX} 1"  # the app's identity in Dexcom mode
MULTI_NAMES = frozenset(f"{SENSOR_PREFIX} {n}" for n in (1, 2, 3))

MODE_LINE = re.compile(r"main: board mode -> (dexcom|multi)")


def classify_adverts(names: Iterable[str], g6_name: str) -> tuple[str | None, str]:
    """``(mode, detail)`` for the set of advertised names: the mode they show, or ``None`` with what
    was wrong. Dexcom mode shows the G6 name and only the app's sensor; Multi shows exactly the
    three sensors and no G6."""
    seen = set(names)
    nordic = {n for n in seen if n.startswith(SENSOR_PREFIX)}
    has_g6 = g6_name in seen
    if has_g6 and nordic == {APP_NAME}:
        return DEXCOM, ""
    if not has_g6 and nordic == MULTI_NAMES:
        return MULTI, ""
    parts = []
    if has_g6:
        parts.append(f"{g6_name} advertises")
    parts.append(f"sensors seen: {sorted(nordic) or 'none'}")
    return None, "; ".join(parts)


def parse_mode_lines(console: str) -> list[str]:
    """The board's logged mode switches, in order (``multi`` / ``dexcom``)."""
    return MODE_LINE.findall(console)


def score_switches(expected: list[str], logged: list[str]) -> list[str]:
    """Problems between the switches the test asked for and the ones the board logged."""
    if logged == expected:
        return []
    problems = [f"asked for {expected}, the board logged {logged}"]
    for i, (want, got) in enumerate(zip(expected, logged, strict=False)):
        if want != got:
            problems.append(f"switch {i + 1}: asked for {want}, the board went {got}")
            break
    return problems


def fingerprint_diff(
    before: dict[int, dict[str, str]], after: dict[int, dict[str, str]]
) -> list[str]:
    """What changed in the slots' stored configuration (``{slot: {characteristic: hex}}``)."""
    changes = []
    for slot in sorted(set(before) | set(after)):
        old, new = before.get(slot, {}), after.get(slot, {})
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                changes.append(f"slot {slot + 1} {key}: {old.get(key)} -> {new.get(key)}")
    return changes


def timing_summary(seconds: list[float]) -> str:
    """``n / median / max`` of the times a switch took, for the report."""
    if not seconds:
        return "n/a"
    return f"n={len(seconds)} median {statistics.median(seconds):.1f} s max {max(seconds):.1f} s"


@dataclass
class Check:
    """One thing checked in one cycle."""

    cycle: int
    kind: str
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Results:
    """Everything the run recorded."""

    checks: list[Check] = field(default_factory=list)
    to_dexcom_s: list[float] = field(default_factory=list)  # write -> expected adverts seen
    to_multi_s: list[float] = field(default_factory=list)
    cycles: int = 0
    aborted: str = ""

    def add(self, cycle: int, kind: str, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(cycle, kind, name, ok, detail))
        return ok

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def failed_cycles(self) -> set[int]:
        return {c.cycle for c in self.failures}


def verdict(results: Results) -> list[str]:
    """The failure lines: every failed check, grouped by name, and an abort if there was one."""
    lines = []
    if results.aborted:
        lines.append(f"the run stopped early: {results.aborted}")
    by_name: dict[str, list[Check]] = {}
    for check in results.failures:
        by_name.setdefault(check.name, []).append(check)
    for name, failed in sorted(by_name.items()):
        cycles = ", ".join(str(c.cycle) for c in failed[:12])
        more = "" if len(failed) <= 12 else f" and {len(failed) - 12} more"
        lines.append(
            f"{name}: failed in {len(failed)} cycle(s) ({cycles}{more}); {failed[0].detail}"
        )
    return lines
