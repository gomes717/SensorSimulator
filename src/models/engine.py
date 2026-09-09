"""Runs the pure physiological model in real time, in parallel with the BLE-connected
board, so the app can show an "expected" glucose trace alongside the "received" one.

Deliberately mirrors the on-device timing rules (see the MCU's model_thread.c):
ticks once per wall-clock second; dt_min per tick is 1/60 in normal mode (1 real
second = 1 simulated second) or 1.0 in fast mode (1 real second = 1 simulated
minute). No sensor noise is applied here — this is the noiseless "true" glucose
that the on-device sensor model itself samples from, so it also carries no
insulin-bolus dosing beyond each model's own constant steady-state basal (the
app only lets the user configure the model/sensor/food/exercise, not a pump).

The per-tick simulation logic lives in :class:`ModelStepper`, which has no
threads, no sleeping and no wall clock — :class:`SimulationEngine` drives it once
per real second, tests drive it step by step (see
``.scratch/thesis-stabilization/issues/01-extract-engine-step-seam.md``).
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from models import cambridge, deichmann, dexcom_csv, food_log_csv, royparker, uva_padova
from models.types import ModelId, PersonProfile

# Report-only spread for a Food Log meal in CSV playback — matches the
# firmware's CSV_FOODLOG_SPREAD_MIN so the app's food/exercise graph shows the
# same shape the board reports.
CSV_FOODLOG_SPREAD_MIN = 30.0

# Ceiling on a single explicit-Euler integration step, in simulated minutes.
# A large dt_min (high speed multiplier) makes the ODEs diverge — at x300 the
# step is 5 sim-min and Cambridge blows up to ~600 mg/dL. tick() sub-steps so
# every integration step is <= this. Must match MODEL_SUBSTEP_MAX_MIN in the
# firmware's model_thread.c so the "expected" and "received" lines stay
# identical at every speed. x1..x60 (dt_min <= 1) are unaffected (nsub == 1).
MODEL_SUBSTEP_MAX_MIN = 1.0


def load_csv_window(profile: PersonProfile) -> tuple[list[int], int, list[tuple[int, float]]]:
    """Resolve a profile's CSV assignment into (glucose_samples, interval_s, foodlog).

    ``glucose_samples`` are integer mg/dL on a fixed ``interval_s`` grid over the
    assigned 24 h window (Dexcom cadence, forward-filled). ``foodlog`` is
    ``[(offset_s, carbs_g), ...]`` from the matching Food Log CSV, if any.
    Returns ``([], 0, [])`` when the path/window is missing or unreadable — used
    by both the local replay engine and the board-upload path so they always
    agree on the bytes.
    """
    path = getattr(profile, "csv_path", None)
    start_iso = getattr(profile, "csv_window_start_iso", None)
    if not path or not start_iso:
        return [], 0, []
    try:
        start = datetime.fromisoformat(start_iso)
        rows = dexcom_csv.read_egv(path)
        samples = dexcom_csv.resample(rows, start, dexcom_csv.DEFAULT_INTERVAL_S)
    except (ValueError, OSError):
        return [], 0, []

    # Food log: explicit path if set, otherwise the sibling that shares the
    # glucose CSV's ID (Dexcom_001.csv -> Food_Log_001.csv) — no separate pick.
    food_path = getattr(profile, "food_log_path", None) or food_log_csv.matching_food_log_path(path)
    foodlog: list[tuple[int, float]] = []
    if food_path:
        try:
            foodlog = food_log_csv.slice_window(food_log_csv.read_food_log(food_path), start)
        except (ValueError, OSError):
            foodlog = []
    return samples, dexcom_csv.DEFAULT_INTERVAL_S, foodlog


def _in_daily_window(time_of_day_min: float, start_min: float, duration_min: float) -> bool:
    """True if the recurring daily clock *time_of_day_min* falls in [start, start+duration)."""
    end = start_min + duration_min
    t = time_of_day_min % 1440.0
    if end <= 1440.0:
        return start_min <= t < end
    return t >= start_min or t < (end - 1440.0)


def _cambridge_step(state, params, carbs, iir, _exercise_pct, _hr_bpm, _t_sim_min, dt_min) -> None:
    cambridge.step(state, params, carbs, iir, dt_min)


def _uva_padova_step(state, params, carbs, iir, _exercise_pct, _hr_bpm, _t_sim_min, dt_min) -> None:
    uva_padova.step(state, params, carbs, iir / 60.0, dt_min)


def _royparker_step(state, params, carbs, iir, exercise_pct, _hr_bpm, t_sim_min, dt_min) -> None:
    royparker.step(state, params, carbs, iir, exercise_pct, t_sim_min, dt_min)


def _deichmann_step(state, params, carbs, iir, _exercise_pct, hr_bpm, _t_sim_min, dt_min) -> None:
    deichmann.step(state, params, carbs, iir, hr_bpm, dt_min)


@dataclass
class _ModelAdapter:
    """Uniform interface over the four model modules, whose native APIs differ slightly
    (rate-fed vs. impulse-fed meals, exercise vs. no exercise input, basal source)."""

    default_params: Callable[[], dict[str, float]]
    init_state: Callable[[dict[str, float]], Any]
    glucose_mg_dl: Callable[[Any, dict[str, float]], float]
    rate_fed: bool  # True: carbs_g_per_min continuous; False: carbs_g pulse on the firing tick
    step: Callable[..., None]
    basal_u_per_h: Callable[[dict[str, float]], float]


_ADAPTERS: dict[ModelId, _ModelAdapter] = {
    ModelId.CAMBRIDGE: _ModelAdapter(
        default_params=cambridge.default_params,
        init_state=cambridge.init_state,
        glucose_mg_dl=cambridge.glucose_mg_dl,
        rate_fed=True,
        step=_cambridge_step,
        basal_u_per_h=cambridge.basal_iir_u_per_h,
    ),
    ModelId.UVA_PADOVA: _ModelAdapter(
        default_params=uva_padova.default_params,
        init_state=uva_padova.init_state,
        glucose_mg_dl=uva_padova.glucose_mg_dl,
        rate_fed=True,
        step=_uva_padova_step,
        basal_u_per_h=uva_padova.basal_iir_u_per_h,
    ),
    ModelId.ROYPARKER: _ModelAdapter(
        default_params=royparker.default_params,
        init_state=royparker.init_state,
        glucose_mg_dl=lambda s, _p: royparker.glucose_mg_dl(s),
        rate_fed=False,
        step=_royparker_step,
        basal_u_per_h=lambda p: p["u1b"],
    ),
    ModelId.DEICHMANN: _ModelAdapter(
        default_params=deichmann.default_params,
        init_state=deichmann.init_state,
        glucose_mg_dl=lambda s, _p: deichmann.glucose_mg_dl(s),
        rate_fed=False,
        step=_deichmann_step,
        basal_u_per_h=lambda p: p["IIRb"],
    ),
}


@dataclass
class TickResult:
    """One tick's output — the payload of ``SimulationEngine.expected_reading``."""

    timestamp: str
    glucose: float
    carbs_rate: float
    exercise_pct: float


@dataclass
class _CsvReplay:
    """Per-run state for the CSV data source."""

    samples: list[int]
    interval_s: int
    span_s: int
    foodlog: list[tuple[int, float]]
    pending: list[tuple[int, float]]  # (offset_s, carbs_g), consumed as the clock passes
    active_meals: list[dict[str, float]]


@dataclass
class _ModelRun:
    """Per-run state for the physiological model data source."""

    adapter: _ModelAdapter
    params: dict[str, float]
    state: Any
    basal: float
    last_fired_day: dict[int, int]


class ModelStepper:
    """One profile's simulation, advanced one tick at a time.

    No threads, no sleeping, no wall clock: ``tick(dt_min, now_iso)`` runs exactly
    one iteration of the old ``_run_model`` / ``_run_csv`` loop body and advances
    the sim clock by ``dt_min``. ``SimulationEngine`` calls it once per real
    second; tests call it directly with an injected ``dt_min`` and timestamp.

    ``data_source == "csv"`` with a readable window replays that window verbatim
    (the food log surfaced report-only on the carbs channel); otherwise the
    physiological model runs. Instant food/exercise/PISA events are injected via
    the thread-safe ``add_instant_*`` methods and consumed on the next tick, the
    same as the firmware's instant-event slots.
    """

    def __init__(self, profile: PersonProfile) -> None:
        self._profile = profile
        self.sim_clock_min = 0.0

        # Instant (one-shot, non-recurring) food/exercise/PISA events injected
        # mid-run. Guarded by a plain lock since add_instant_* is called from the
        # GUI thread while tick() runs on the SimulationEngine QThread.
        self._instant_lock = threading.Lock()
        self._instant_food: list[dict[str, Any]] = []
        self._instant_exercise: list[dict[str, Any]] = []
        self._instant_pisa: list[dict[str, Any]] = []
        # When a board is connected, its Food/Exercise Status is the single
        # source of truth for the meal/exercise input to the "expected" model —
        # (carbs_g_per_min, exercise_pct), or None in Model Only mode where the
        # profile's own recurring schedule drives instead.
        self._board_food_ex: tuple[float, float] | None = None

        self._mode = "model"
        self._csv: _CsvReplay | None = None
        self._model: _ModelRun | None = None

        # A CSV-backed person never runs the physiological model: the "expected"
        # line is the recording, or nothing until a CSV window is assigned —
        # not a model drifting in the background (user report 2026-09-09).
        if getattr(profile, "data_source", "model") == "csv":
            samples, interval_s, foodlog = load_csv_window(profile)
            if samples:
                self._mode = "csv"
                self._csv = _CsvReplay(
                    samples=samples,
                    interval_s=interval_s,
                    span_s=max(1, len(samples) * interval_s),
                    foodlog=foodlog,
                    pending=sorted(foodlog),
                    active_meals=[],
                )
            else:
                self._mode = "idle"  # CSV requested, none assigned — emit nothing

        if self._mode == "model":
            adapter = _ADAPTERS[profile.model_id]
            params = {**adapter.default_params(), **profile.params}
            basal = (
                profile.basal_u_per_h
                if profile.basal_u_per_h is not None
                else adapter.basal_u_per_h(params)
            )
            self._model = _ModelRun(
                adapter=adapter,
                params=params,
                state=adapter.init_state(params),
                basal=basal,
                last_fired_day={},
            )

    @property
    def mode(self) -> str:
        """``"model"``, ``"csv"`` (replay a recording) or ``"idle"`` (CSV-backed
        person with no window assigned — emits NaN, no model) — resolved once."""
        return self._mode

    # -- instant events (thread-safe) ------------------------------------

    def add_instant_food(self, duration_min: float, carbs_g: float) -> None:
        """Start an instant carb bolus right now, without resetting the simulation.

        Thread-safe; call from any thread. Rate-fed models spread carbs_g evenly
        over duration_min; impulse-fed models deliver the full amount once, on
        the next tick — same semantics as the firmware's instant food events.
        """
        duration = max(float(duration_min), 1.0)
        with self._instant_lock:
            self._instant_food.append(
                {
                    "remaining_min": duration,
                    "duration_min": duration,
                    "carbs_g": float(carbs_g),
                    "delivered": False,
                }
            )

    def add_instant_exercise(self, duration_min: float, intensity_pct: float) -> None:
        """Start an instant exercise bout right now, without resetting the simulation.

        Thread-safe; call from any thread. While active, contributes
        max(intensity_pct, whatever the recurring schedule currently gives).
        """
        duration = max(float(duration_min), 1.0)
        with self._instant_lock:
            self._instant_exercise.append(
                {"remaining_min": duration, "intensity_pct": float(intensity_pct)}
            )

    def add_instant_pisa(self, duration_min: float, depth_frac: float) -> None:
        """Start a transient PISA attenuation now, without resetting the simulation.

        Thread-safe. Multiplies the emitted glucose by
        (1 - depth_frac * sin(pi * elapsed/duration)) while active — the same
        smooth false-low shape the firmware applies to the sensor reading.
        """
        duration = max(float(duration_min), 1.0)
        with self._instant_lock:
            self._instant_pisa.append(
                {
                    "remaining_min": duration,
                    "duration_min": duration,
                    "depth": max(0.0, min(1.0, float(depth_frac))),
                }
            )

    def set_board_food_exercise(self, carbs_g_per_min: float, exercise_pct: float) -> None:
        """Latest Food/Exercise Status from the connected board. While set, this
        drives the model's meal + exercise input instead of the profile's own
        recurring schedule — so the "expected" line reflects whatever the board
        is actually doing (its schedule *and* any instant events), not a
        possibly-stale local copy. Thread-safe."""
        with self._instant_lock:
            self._board_food_ex = (float(carbs_g_per_min), float(exercise_pct))

    def _pisa_factor(self, dt_min: float) -> float:
        """Combined attenuation of all active PISA bouts this tick; decays them."""
        factor = 1.0
        still_active = []
        for p in self._instant_pisa:
            frac = (p["duration_min"] - p["remaining_min"]) / p["duration_min"]
            frac = min(1.0, max(0.0, frac))
            factor *= 1.0 - p["depth"] * math.sin(frac * math.pi)
            p["remaining_min"] -= dt_min
            if p["remaining_min"] > 0.0:
                still_active.append(p)
        self._instant_pisa = still_active
        return max(0.0, factor)

    # -- stepping ------------------------------------------------------

    def tick(self, dt_min: float, now_iso: str) -> TickResult:
        """Advance the simulation by one tick of *dt_min* simulated minutes.

        *now_iso* is the wall-clock timestamp to stamp on the reading (the driver
        passes ``datetime.now(UTC)``; tests pass a fixed value).
        """
        if self._mode == "csv":
            return self._tick_csv(dt_min, now_iso)
        if self._mode == "idle":  # CSV-backed but no window assigned — no model
            self.sim_clock_min += dt_min
            return TickResult(now_iso, float("nan"), 0.0, 0.0)
        return self._tick_model(dt_min, now_iso)

    def _tick_csv(self, dt_min: float, now_iso: str) -> TickResult:
        c = self._csv
        assert c is not None
        samples = c.samples

        t_s = self.sim_clock_min * 60.0
        loop_s = t_s % c.span_s

        row = int(loop_s / c.interval_s) % len(samples)
        glucose = float(samples[row])

        # Food log (report-only): start a 30-min spread when the clock reaches a
        # meal; also fold in "Insert Food Now" instant events.
        while c.pending and c.pending[0][0] <= loop_s:
            _, carbs_g = c.pending.pop(0)
            c.active_meals.append(
                {
                    "remaining_min": CSV_FOODLOG_SPREAD_MIN,
                    "rate": carbs_g / CSV_FOODLOG_SPREAD_MIN,
                }
            )
        if not c.pending and loop_s < dt_min * 60.0:
            c.pending = sorted(c.foodlog)  # window looped — re-arm

        with self._instant_lock:
            for inst in self._instant_food:
                dur = max(inst["duration_min"], 1.0)
                c.active_meals.append({"remaining_min": dur, "rate": inst["carbs_g"] / dur})
            self._instant_food = []  # CSV mode: report-only, one hand-off
            glucose *= self._pisa_factor(dt_min)  # PISA attenuates the sensor, CSV or not

        carbs_rate = 0.0
        still_active = []
        for meal in c.active_meals:
            carbs_rate += meal["rate"]
            meal["remaining_min"] -= dt_min
            if meal["remaining_min"] > 0.0:
                still_active.append(meal)
        c.active_meals = still_active

        print(
            f"[engine:csv] t_sim={self.sim_clock_min:.2f}min row={row} glucose={glucose:.1f} "
            f"carbs={carbs_rate:.3f}"
        )
        self.sim_clock_min += dt_min
        return TickResult(now_iso, glucose, carbs_rate, 0.0)

    def _tick_model(self, dt_min: float, now_iso: str) -> TickResult:  # noqa: C901
        # Irreducibly branchy: the sub-step loop crossed with the four model
        # adapters' feed styles (rate-fed vs impulse-fed, meal vs meal-rest).
        m = self._model
        assert m is not None
        adapter = m.adapter
        params = m.params
        time_of_day = self.sim_clock_min % 1440.0
        day_index = int(self.sim_clock_min // 1440.0)

        carbs = 0.0
        for i, ev in enumerate(self._profile.food_events):
            if adapter.rate_fed:
                if _in_daily_window(time_of_day, ev.time_of_day_min, ev.duration_min):
                    carbs += ev.carbs_g / ev.duration_min
            elif m.last_fired_day.get(i) != day_index and _in_daily_window(
                time_of_day, ev.time_of_day_min, dt_min
            ):
                carbs += ev.carbs_g
                m.last_fired_day[i] = day_index

        exercise_pct = 0.0
        hr_bpm = params.get("HRb", 80.0)
        for ev in self._profile.exercise_events:
            if _in_daily_window(time_of_day, ev.time_of_day_min, ev.duration_min):
                exercise_pct = ev.intensity_pct
                hr_bpm = params.get("HRb", 80.0) + ev.intensity_pct / 100.0 * 80.0

        with self._instant_lock:
            still_active_food = []
            for inst in self._instant_food:
                if adapter.rate_fed:
                    carbs += inst["carbs_g"] / inst["duration_min"]
                elif not inst["delivered"]:
                    carbs += inst["carbs_g"]
                    inst["delivered"] = True
                inst["remaining_min"] -= dt_min
                if inst["remaining_min"] > 0.0:
                    still_active_food.append(inst)
            self._instant_food = still_active_food

            still_active_exercise = []
            for inst in self._instant_exercise:
                if inst["intensity_pct"] > exercise_pct:
                    exercise_pct = inst["intensity_pct"]
                    hr_bpm = params.get("HRb", 80.0) + inst["intensity_pct"] / 100.0 * 80.0
                inst["remaining_min"] -= dt_min
                if inst["remaining_min"] > 0.0:
                    still_active_exercise.append(inst)
            self._instant_exercise = still_active_exercise

            pisa_factor = self._pisa_factor(dt_min)
            board_food_ex = self._board_food_ex

        # A connected board's Food/Exercise Status wins: it already reflects the
        # board's own schedule + any instant events, so the "expected" line
        # tracks what the board is really doing regardless of the local profile.
        if board_food_ex is not None:
            ext_carbs_rate, exercise_pct = board_food_ex
            carbs = ext_carbs_rate if adapter.rate_fed else ext_carbs_rate * dt_min
            hr_bpm = params.get("HRb", 80.0) + exercise_pct / 100.0 * 80.0

        # Sub-step the ODE so a large dt_min can't make explicit Euler diverge —
        # mirrors the firmware's MODEL_SUBSTEP_MAX_MIN loop in model_thread.c.
        # Impulse-fed carbs are a one-shot mass, delivered on the first sub-step
        # only; rate-fed carbs (g/min) and the exercise level apply to every one.
        nsub = max(1, math.ceil(dt_min / MODEL_SUBSTEP_MAX_MIN))
        sub_dt = dt_min / nsub
        carbs_rest = carbs if adapter.rate_fed else 0.0
        for k in range(nsub):
            adapter.step(
                m.state,
                params,
                carbs if k == 0 else carbs_rest,
                m.basal,
                exercise_pct,
                hr_bpm,
                self.sim_clock_min,
                sub_dt,
            )
        glucose = adapter.glucose_mg_dl(m.state, params) * pisa_factor

        # For the food/exercise graph: rate-fed models already carry a g/min rate;
        # impulse-fed models deliver the whole meal on one tick, so express that
        # tick's delivery as an equivalent rate too, for a comparable plot.
        carbs_rate = carbs if adapter.rate_fed else (carbs / dt_min if carbs > 0.0 else 0.0)

        print(
            f"[engine] t_sim={self.sim_clock_min:.2f}min dt={dt_min:.4f} "
            f"glucose={glucose:.2f} carbs={carbs_rate:.3f} ex={exercise_pct:.1f}"
        )
        self.sim_clock_min += dt_min
        return TickResult(now_iso, glucose, carbs_rate, exercise_pct)


class SimulationEngine(QThread):
    """Drives a :class:`ModelStepper` once per wall-clock second and emits readings."""

    # ISO timestamp, glucose_mg_dl, carbs_g_per_min (0 outside a rate-fed meal window,
    # or the equivalent instantaneous rate for a firing impulse-fed meal), exercise_pct
    expected_reading = pyqtSignal(str, float, float, float)

    def __init__(self, profile: PersonProfile, speed_mult: float, parent=None) -> None:
        """Store the profile + speed multiplier to run; call start() to begin ticking.

        *speed_mult* (x1..x1000) scales simulated time per 1 Hz tick exactly as
        on the MCU: dt_min = (1/60) * speed_mult.

        Call set_paused(True) before start() to have a freshly (re)created
        engine sit ready-but-idle until the app's Start button resumes it,
        keeping the local clock in lockstep with the board's own run-state
        (see PROTOCOL_SPEC.md's "Run state" section).
        """
        super().__init__(parent)
        self._speed_mult = max(1.0, min(1000.0, float(speed_mult)))
        self._paused = False
        self._stepper = ModelStepper(profile)

    def stop(self) -> None:
        """Request the tick loop to end after its current iteration."""
        self.requestInterruption()

    def add_instant_food(self, duration_min: float, carbs_g: float) -> None:
        """Inject an instant carb bolus now (thread-safe) — see ModelStepper.add_instant_food."""
        self._stepper.add_instant_food(duration_min, carbs_g)

    def add_instant_exercise(self, duration_min: float, intensity_pct: float) -> None:
        """Inject an instant exercise bout now (thread-safe)."""
        self._stepper.add_instant_exercise(duration_min, intensity_pct)

    def add_instant_pisa(self, duration_min: float, depth_frac: float) -> None:
        """Inject a transient PISA attenuation now (thread-safe)."""
        self._stepper.add_instant_pisa(duration_min, depth_frac)

    def set_board_food_exercise(self, carbs_g_per_min: float, exercise_pct: float) -> None:
        """Feed the board's latest Food/Exercise Status to the model (thread-safe)."""
        self._stepper.set_board_food_exercise(carbs_g_per_min, exercise_pct)

    def pause(self) -> None:
        """Freeze the simulation clock and model state in place until resume()."""
        self._paused = True

    def resume(self) -> None:
        """Continue ticking from exactly where pause() left off."""
        self._paused = False

    def set_paused(self, paused: bool) -> None:
        """Set the initial paused state before start() — see __init__."""
        self._paused = paused

    def run(self) -> None:
        """Tick once per wall-clock second until stop() is called.

        Timing only: the per-tick simulation lives in :class:`ModelStepper`.
        """
        while not self.isInterruptionRequested():
            if self._paused:
                self.msleep(100)
                continue

            tick_start = time.monotonic()
            dt_min = (1.0 / 60.0) * self._speed_mult
            now_iso = datetime.now(UTC).isoformat(timespec="seconds")
            res = self._stepper.tick(dt_min, now_iso)
            self.expected_reading.emit(res.timestamp, res.glucose, res.carbs_rate, res.exercise_pct)

            elapsed = time.monotonic() - tick_start
            self.msleep(max(0, int(1000 - elapsed * 1000)))


class EnginePool(QObject):
    """One :class:`SimulationEngine` per occupied sensor slot.

    Re-emits each engine's ``expected_reading`` tagged with its slot. One shared
    speed multiplier; ``pause_all`` / ``resume_all`` / ``stop_all`` fan out to
    every engine. Rebuilt wholesale whenever the slot->profile mapping, the
    speed, or the mode changes (see issue 04). A single-sensor / no-board setup
    is just one entry, keyed ``0``.
    """

    # slot, then the SimulationEngine.expected_reading payload
    expected_reading = pyqtSignal(int, str, float, float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._engines: dict[int, SimulationEngine] = {}
        self._speed_mult = 1.0
        self._paused = True

    @property
    def slots(self) -> list[int]:
        return sorted(self._engines)

    def is_empty(self) -> bool:
        return not self._engines

    def rebuild(
        self, profiles: dict[int, PersonProfile], speed_mult: float, *, paused: bool
    ) -> None:
        """Stop every engine and start a fresh one per slot in *profiles*."""
        self.stop_all()
        self._speed_mult = max(1.0, min(1000.0, float(speed_mult)))
        self._paused = paused
        for slot, profile in profiles.items():
            eng = SimulationEngine(profile, self._speed_mult, self)
            eng.expected_reading.connect(
                lambda ts, g, c, e, s=slot: self.expected_reading.emit(s, ts, g, c, e)
            )
            eng.set_paused(paused)
            eng.start()
            self._engines[slot] = eng

    def stop_all(self) -> None:
        for eng in self._engines.values():
            with contextlib.suppress(TypeError):
                eng.expected_reading.disconnect()
            eng.stop()
            eng.wait(2000)
        self._engines.clear()

    def pause_all(self) -> None:
        self._paused = True
        for eng in self._engines.values():
            eng.pause()

    def resume_all(self) -> None:
        self._paused = False
        for eng in self._engines.values():
            eng.resume()

    def add_instant_food(self, slot: int | None, duration_min: float, carbs_g: float) -> None:
        for eng in self._targets(slot):
            eng.add_instant_food(duration_min, carbs_g)

    def add_instant_exercise(
        self, slot: int | None, duration_min: float, intensity_pct: float
    ) -> None:
        for eng in self._targets(slot):
            eng.add_instant_exercise(duration_min, intensity_pct)

    def add_instant_pisa(self, slot: int | None, duration_min: float, depth_frac: float) -> None:
        for eng in self._targets(slot):
            eng.add_instant_pisa(duration_min, depth_frac)

    def set_board_food_exercise(
        self, slot: int, carbs_g_per_min: float, exercise_pct: float
    ) -> None:
        """Route one slot's Food/Exercise Status from the board into its engine."""
        eng = self._engines.get(slot)
        if eng is not None:
            eng.set_board_food_exercise(carbs_g_per_min, exercise_pct)

    def _targets(self, slot: int | None) -> list[SimulationEngine]:
        if slot is None:
            return list(self._engines.values())
        eng = self._engines.get(slot)
        return [eng] if eng is not None else []
