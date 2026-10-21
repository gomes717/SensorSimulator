"""Scoring for the overnight Dexcom-mode test (``overnight_dexcom.py``): pure functions, no BLE.

Three views of the same readings are compared:

* **published** — what the board's console says it published (``dexcom reading seq=… ts=…s
  glucose=…``), with the wall time the monitor first saw each line;
* **G6 reads** — what the xDrip-like client got over the G6 characteristics;
* **app values** — what the app-like link received over the CGMS characteristic.

A correct board publishes one reading per 300 simulated seconds with consecutive ``seq``, serves
that reading to every G6 client until the next one, and sends the same value to the app.
"""

from __future__ import annotations

import itertools
import re
import statistics
from dataclasses import dataclass

PERIOD_S = 300
GLUCOSE_MIN, GLUCOSE_MAX = 40, 400
MAX_STEP_MGDL = 80  # between two consecutive readings: a meal can move glucose, a bug jumps further
APP_MATCH_S = 20.0  # the app value arrives within this of the board publishing it
READ_BUDGET_S = 90.0  # a G6 read this long after publication is a miss
CLOSED_OBJECT = "-2147483629"  # Windows' "the object was closed" (RO_E_CLOSED)
RECOVERY_BUDGET_S = 15.0  # a closed-object drop is harmless if the link is back this fast ...
MAX_CLOSED_PER_HOUR = 2.0  # ... and it does not keep happening


@dataclass(frozen=True)
class Published:
    """One reading the board logged."""

    seq: int
    ts_s: int
    glucose: int
    wall: float  # when the monitor first saw the console line


@dataclass(frozen=True)
class G6Read:
    """One successful G6 read."""

    wall: float
    seq: int
    ts_s: int
    glucose: int
    attempts: int = 1


@dataclass(frozen=True)
class AppValue:
    """One glucose notification the app-like link received."""

    wall: float
    glucose: float


@dataclass
class LinkDrop:
    """One time the app-like link went down. ``error`` is empty when the board simply hung up."""

    wall: float
    error: str = ""
    where: str = ""  # the step that failed: scan / connect / subscribe / idle / mode read
    since_xdrip_s: float | None = None  # how long after the xDrip-like client last connected
    recovered_wall: float | None = None  # when the next connection had its subscription again

    @property
    def closed_object(self) -> bool:
        return CLOSED_OBJECT in self.error

    @property
    def recovered_s(self) -> float | None:
        return None if self.recovered_wall is None else self.recovered_wall - self.wall

    @property
    def tolerated(self) -> bool:
        """A closed-object drop that came back inside the budget costs at most the readings
        published while it was down; those are not held against the board or the app."""
        return (
            self.closed_object
            and self.recovered_s is not None
            and self.recovered_s <= RECOVERY_BUDGET_S
        )

    def excuses(self, pub_wall: float) -> bool:
        """True if a tolerated drop can account for the app missing a reading published at
        *pub_wall* (the app expects it between 2 s before and ``APP_MATCH_S`` after)."""
        if not self.tolerated or self.recovered_wall is None:
            return False
        return self.wall - APP_MATCH_S <= pub_wall <= self.recovered_wall + 2.0

    def describe(self) -> str:
        parts = [self.error or "the link dropped"]
        if self.where:
            parts.append(f"during {self.where}")
        recovered = self.recovered_s
        parts.append("not back yet" if recovered is None else f"back after {recovered:.1f} s")
        if self.since_xdrip_s is not None:
            parts.append(f"{self.since_xdrip_s:.0f} s after the xDrip-like client's last connect")
        return ", ".join(parts)


def score_drops(drops: list[LinkDrop], hours: float) -> list[str]:
    """Problems in how the app-like link behaved: closed-object drops that were slow to recover,
    or that keep happening. Other drops are judged only through the app coverage."""
    problems = [
        f"closed-object drop at {d.wall:.0f} not recovered in {RECOVERY_BUDGET_S:.0f} s "
        f"({d.describe()})"
        for d in drops
        if d.closed_object and not d.tolerated
    ]
    closed = sum(1 for d in drops if d.closed_object)
    if closed / max(1.0, hours) > MAX_CLOSED_PER_HOUR:
        problems.append(
            f"{closed} closed-object drops in {hours:.1f} h (limit {MAX_CLOSED_PER_HOUR:g}/h)"
        )
    return problems


READING_LINE = re.compile(r"dexcom reading seq=(\d+) ts=(\d+)s glucose=(\d+)")
ANOMALY_LINE = re.compile(r"unknown op|bad crc|ASSERT|FATAL|Fault|Security failed|oops", re.I)
BOOT_LINE = "Starting Bluetooth Peripheral CGM simulator"


def parse_console(text: str) -> tuple[list[tuple[int, int, int]], int, list[str]]:
    """``(readings, boots, anomalies)`` from the console text: the ``(seq, ts, glucose)`` lines, how
    many times the board booted, and every line that looks like a fault."""
    readings = [(int(s), int(t), int(g)) for s, t, g in READING_LINE.findall(text)]
    lines = text.splitlines()
    boots = sum(1 for line in lines if BOOT_LINE in line)
    return readings, boots, [line.strip() for line in lines if ANOMALY_LINE.search(line)]


def score_publications(pubs: list[Published]) -> list[str]:
    """Problems in the board's own sequence of readings."""
    problems: list[str] = []
    for prev, cur in itertools.pairwise(pubs):
        if cur.seq == 0 and cur.ts_s == 0:
            continue  # the grid restarted (a mode switch or a reset): checked elsewhere
        if cur.seq != prev.seq + 1:
            problems.append(f"seq jumped {prev.seq} -> {cur.seq}")
        if cur.ts_s - prev.ts_s != PERIOD_S:
            problems.append(f"ts stepped {cur.ts_s - prev.ts_s} s at seq {cur.seq}, not {PERIOD_S}")
        if abs(cur.glucose - prev.glucose) > MAX_STEP_MGDL:
            problems.append(f"glucose moved {prev.glucose} -> {cur.glucose} at seq {cur.seq}")
    for p in pubs:
        if not GLUCOSE_MIN <= p.glucose <= GLUCOSE_MAX:
            problems.append(f"glucose {p.glucose} out of range at seq {p.seq}")
    return problems


@dataclass
class ReadScore:
    """How well the G6 reads covered the publications."""

    published: int
    covered: int
    problems: list[str]
    latencies_s: list[float]

    @property
    def coverage(self) -> float:
        return self.covered / self.published if self.published else 1.0


def score_reads(pubs: list[Published], reads: list[G6Read]) -> ReadScore:
    """Every publication should be read once, correctly, soon after it is published."""
    problems: list[str] = []
    latencies: list[float] = []
    covered = 0
    for pub in pubs:
        mine = [r for r in reads if r.seq == pub.seq and r.ts_s == pub.ts_s]
        if not mine:
            problems.append(f"seq {pub.seq} was never read over G6")
            continue
        first = min(mine, key=lambda r: r.wall)
        if first.glucose != pub.glucose:
            problems.append(
                f"seq {pub.seq}: G6 read {first.glucose}, the board published {pub.glucose}"
            )
            continue
        latency = first.wall - pub.wall
        if latency > READ_BUDGET_S:
            problems.append(f"seq {pub.seq} was read {latency:.0f} s after publication")
        covered += 1
        latencies.append(latency)
    known = {(p.seq, p.ts_s) for p in pubs}
    for r in reads:
        if (r.seq, r.ts_s) not in known:
            problems.append(
                f"a G6 read returned seq {r.seq} ts {r.ts_s}, which the board never logged"
            )
    return ReadScore(len(pubs), covered, problems, latencies)


def score_app(pubs: list[Published], values: list[AppValue]) -> tuple[int, list[str]]:
    """``(matched, problems)``: each publication should reach the app, with the same value."""
    problems: list[str] = []
    matched = 0
    for pub in pubs:
        near = [v for v in values if -2.0 <= v.wall - pub.wall <= APP_MATCH_S]
        if not near:
            problems.append(f"seq {pub.seq} never reached the app")
        elif not any(abs(v.glucose - pub.glucose) < 0.6 for v in near):
            problems.append(
                f"seq {pub.seq}: the app got {[v.glucose for v in near]}, "
                f"the board published {pub.glucose}"
            )
        else:
            matched += 1
    return matched, problems


def latency_summary(latencies_s: list[float]) -> str:
    """``min / median / p95 / max`` of the read latencies, for the report."""
    if not latencies_s:
        return "n/a"
    ordered = sorted(latencies_s)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
    return (
        f"min {ordered[0]:.1f} s / median {statistics.median(ordered):.1f} s / "
        f"p95 {p95:.1f} s / max {ordered[-1]:.1f} s"
    )
