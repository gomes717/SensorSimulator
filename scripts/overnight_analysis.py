"""Pure analysis for the overnight Users test (``scripts/overnight_users.py``): no hardware, no Qt.

It scores one cycle from what was recorded — the board's console ticks, what the app's own
"expected" model emitted, and what arrived over BLE — against the users that were on each slot.
Everything here is a function of its arguments so it can be tested without a board.
"""

from __future__ import annotations

import bisect
import statistics
from dataclasses import dataclass

import e2e_long_3sensor as lg
from e2e_long_3sensor import Tick

from models.types import PersonProfile, SensorId

# --- pass criteria, fixed before the night -------------------------------------------------
APP_VS_BOARD_P99_MIN = 3.5  # mg/dL floor of the limit (see app_limit)
APP_SLACK_TICKS = 2  # the firmware applies a schedule ~2 ticks earlier than the host model
APP_MIN_TICKS = 120  # an epoch shorter than this is too short to judge
EPOCH_PAIR_WINDOW_S = 12.0  # the app's engine and the board's slot must start this close
CSV_LOOP_TOL = 3.0  # mg/dL against the CSV sample at (sim time mod the window)
CSV_LOOP_MIN_SHARE = 0.99
IDEAL_OFFSET_MAX = 2.6  # the Ideal sensor adds (slot - (N-1)/2) * 2 mg/dL (model_thread.c)
NOISY_MIN_STD = 0.3  # a noisy sensor model must actually add noise
LINK_BLIND_TICKS = 3  # ticks around an instant event skipped when comparing the two models


@dataclass(frozen=True)
class AppTick:
    """One tick of the app's expected model for a slot (t_sim counted from its engine start)."""

    t_sim: float
    glucose: float


def board_segments(ticks: list[Tick]) -> list[list[Tick]]:
    """The board's ticks split at every sim-clock reset (a restart or a new configuration)."""
    segments: list[list[Tick]] = []
    for tick in ticks:
        if not segments or tick.t_sim < segments[-1][-1].t_sim - 1e-6:
            segments.append([])
        segments[-1].append(tick)
    return segments


def app_limit(
    profile: PersonProfile, instants: list[lg.Instant], speed: float, host_range: float
) -> float:
    """How far the app's model may sit from the board's: never below APP_VS_BOARD_P99_MIN, and
    never below one tick of the shortest window as a fraction of the excursion (the two engines
    decide differently whether the last tick of a window is inside it)."""
    durations = [e.duration_min for e in profile.food_events if e.duration_min > 0]
    durations += [e.duration_min for e in profile.exercise_events if e.duration_min > 0]
    durations += [e.args[0] for e in instants if e.kind in ("food", "exercise")]
    quantum = (speed / 60.0) / min(durations) if durations else 0.0
    return max(APP_VS_BOARD_P99_MIN, quantum * host_range)


def app_vs_board(
    app: list[AppTick],
    board: list[Tick],
    dt: float,
    masked: list[tuple[float, float]],
    slack: int = APP_SLACK_TICKS,
) -> dict:
    """The app's expected glucose against the board's true glucose x PISA, tick by tick.

    Both start from sim time 0 and step by *dt*, so they are compared at the same sim time, each
    app tick against the closest board value within +-*slack* ticks. Sim-time intervals in
    *masked* (around instant events, which each side applies at its own tick) are left out."""
    if not app or not board or dt <= 0:
        return {"n": 0}
    times = [tick.t_sim for tick in board]
    values = [tick.glucose * tick.pisa for tick in board]
    errors: list[float] = []
    for tick in app:
        if any(lo <= tick.t_sim <= hi for lo, hi in masked):
            continue
        low = bisect.bisect_left(times, tick.t_sim - slack * dt - 1e-6)
        high = bisect.bisect_right(times, tick.t_sim + slack * dt + 1e-6)
        if low >= high:
            continue
        errors.append(min(abs(tick.glucose - values[j]) for j in range(low, high)))
    if not errors:
        return {"n": 0}
    order = sorted(errors)
    return {
        "n": len(errors),
        "mean": statistics.fmean(errors),
        "p99": order[int(0.99 * (len(order) - 1))],
        "max": order[-1],
        "range": max(t.glucose * t.pisa for t in board) - min(t.glucose * t.pisa for t in board),
    }


def pair_epochs(
    app_starts: list[float], segments: list[list[Tick]], window: float = EPOCH_PAIR_WINDOW_S
) -> list[tuple[int, int]]:
    """(app epoch, board segment) pairs that started together: the only ones that can agree.

    An app epoch starts when its engines are rebuilt; a board segment starts when its slot's
    sim clock resets. Both are host times of the same clock."""
    pairs: list[tuple[int, int]] = []
    for e, start in enumerate(app_starts):
        for s, segment in enumerate(segments):
            if segment and abs(segment[0].t - start) <= window:
                pairs.append((e, s))
                break
    return pairs


def csv_loop(profile: PersonProfile, ticks: list[Tick]) -> dict:
    """A replayed recording wraps at the end of its window: past it the board plays the window
    again from the start. Returns the ticks past the end, how many match the sample at
    (sim time mod the window), and whether the run got past the end at all."""
    samples, interval_s, _foodlog = lg.load_csv_window(profile)
    run = lg.last_run(ticks)
    if not samples or interval_s <= 0 or not run:
        return {
            "n": 0,
            "after": 0,
            "ok": 0,
            "ok_all": 0,
            "worst": 0.0,
            "wrapped": False,
            "span_min": 0.0,
            "reached_min": 0.0,
        }
    span_s = len(samples) * interval_s
    ok = ok_all = after = total = 0
    worst = 0.0
    for tick in run:
        t_s = tick.t_sim * 60.0
        row = int((t_s % span_s) // interval_s) % len(samples)
        neighbours = [samples[(row + d) % len(samples)] for d in (-1, 0, 1)]
        err = min(abs(tick.glucose - c) for c in neighbours)
        total += 1
        ok_all += err <= CSV_LOOP_TOL
        past_end = t_s >= span_s
        if past_end:
            after += 1
            worst = max(worst, err)
            ok += err <= CSV_LOOP_TOL
    return {
        "n": total,
        "after": after,
        "ok": ok,
        "ok_all": ok_all,
        "worst": worst,
        "wrapped": after > 0,
        "span_min": span_s / 60.0,
        "reached_min": run[-1].t_sim,
    }


def sensor_noise(sensor_id: SensorId, ticks: list[Tick]) -> dict:
    """What the sensor model did to the reading: ``reading`` against the true glucose x PISA.

    Ideal adds only the board's per-slot offset (a few mg/dL at most); the noisy models must add
    real noise. Returns the max/std of the residual and whether it fits the model."""
    run = lg.last_run(ticks)
    residual = [t.reading - t.glucose * t.pisa for t in run]
    if len(residual) < 30:
        return {"n": len(residual), "ok": None}
    std = statistics.pstdev(residual)
    worst = max(abs(r) for r in residual)
    if sensor_id == SensorId.IDEAL:
        # a slot's constant offset: the spread around it is ~0, and the offset itself is small
        return {
            "n": len(residual),
            "ok": worst <= IDEAL_OFFSET_MAX and std < 0.2,
            "max": worst,
            "std": std,
        }
    return {"n": len(residual), "ok": std >= NOISY_MIN_STD, "max": worst, "std": std}


def masks_for(instants: list[lg.Instant], dt: float) -> list[tuple[float, float]]:
    """Sim-time intervals to leave out when comparing two models around instant events."""
    out = []
    for ev in instants:
        length = ev.args[0] if ev.kind in ("food", "exercise", "pisa") else 0
        out.append((ev.t_sim - LINK_BLIND_TICKS * dt, ev.t_sim + length + 90.0))
    return out
