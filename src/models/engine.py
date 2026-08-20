"""Runs the pure physiological model in real time, in parallel with the BLE-connected
board, so the app can show an "expected" glucose trace alongside the "received" one.

Deliberately mirrors the on-device timing rules (see the MCU's model_thread.c):
ticks once per wall-clock second; dt_min per tick is 1/60 in normal mode (1 real
second = 1 simulated second) or 1.0 in fast mode (1 real second = 1 simulated
minute). No sensor noise is applied here — this is the noiseless "true" glucose
that the on-device sensor model itself samples from, so it also carries no
insulin-bolus dosing beyond each model's own constant steady-state basal (the
app only lets the user configure the model/sensor/food/exercise, not a pump).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal

from models import cambridge, deichmann, royparker, uva_padova
from models.types import ModelId, PersonProfile


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


class SimulationEngine(QThread):
    """Steps one PersonProfile's model once per wall-clock second and emits readings."""

    # ISO timestamp, glucose_mg_dl, carbs_g_per_min (0 outside a rate-fed meal window,
    # or the equivalent instantaneous rate for a firing impulse-fed meal), exercise_pct
    expected_reading = pyqtSignal(str, float, float, float)

    def __init__(self, profile: PersonProfile, fast_mode: bool, parent=None) -> None:
        """Store the profile/mode to run; call start() to begin ticking.

        Call set_paused(True) before start() to have a freshly (re)created
        engine sit ready-but-idle until the app's Start button resumes it,
        keeping the local clock in lockstep with the board's own run-state
        (see PROTOCOL_SPEC.md's "Run state" section).
        """
        super().__init__(parent)
        self._profile = profile
        self._fast_mode = fast_mode
        self._paused = False
        # Instant (one-shot, non-recurring) food/exercise events injected mid-run
        # via add_instant_food()/add_instant_exercise() — mirrors the firmware's
        # model_thread instant-event slots (see PROTOCOL_SPEC.md). Guarded by a
        # plain lock since those are called from the GUI thread while run() below
        # executes on this QThread.
        self._instant_lock = threading.Lock()
        self._instant_food: list[dict[str, Any]] = []
        self._instant_exercise: list[dict[str, Any]] = []

    def stop(self) -> None:
        """Request the tick loop to end after its current iteration."""
        self.requestInterruption()

    def add_instant_food(self, duration_min: float, carbs_g: float) -> None:
        """Start an instant carb bolus right now, without resetting the simulation.

        Thread-safe; call from any thread. Rate-fed models spread carbs_g evenly
        over duration_min; impulse-fed models deliver the full amount once, on
        the next tick — same semantics as the firmware's instant food events.
        """
        duration = max(float(duration_min), 1.0)
        with self._instant_lock:
            self._instant_food.append(
                {"remaining_min": duration, "duration_min": duration, "carbs_g": float(carbs_g), "delivered": False}
            )

    def add_instant_exercise(self, duration_min: float, intensity_pct: float) -> None:
        """Start an instant exercise bout right now, without resetting the simulation.

        Thread-safe; call from any thread. While active, contributes
        max(intensity_pct, whatever the recurring schedule currently gives).
        """
        duration = max(float(duration_min), 1.0)
        with self._instant_lock:
            self._instant_exercise.append({"remaining_min": duration, "intensity_pct": float(intensity_pct)})

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
        """Tick the model once per wall-clock second until stop() is called."""
        adapter = _ADAPTERS[self._profile.model_id]
        params = {**adapter.default_params(), **self._profile.params}
        state = adapter.init_state(params)
        basal = (
            self._profile.basal_u_per_h
            if self._profile.basal_u_per_h is not None
            else adapter.basal_u_per_h(params)
        )

        sim_clock_min = 0.0
        last_fired_day: dict[int, int] = {}

        while not self.isInterruptionRequested():
            if self._paused:
                self.msleep(100)
                continue

            tick_start = time.monotonic()
            dt_min = 1.0 if self._fast_mode else (1.0 / 60.0)
            time_of_day = sim_clock_min % 1440.0
            day_index = int(sim_clock_min // 1440.0)

            carbs = 0.0
            for i, ev in enumerate(self._profile.food_events):
                if adapter.rate_fed:
                    if _in_daily_window(time_of_day, ev.time_of_day_min, ev.duration_min):
                        carbs += ev.carbs_g / ev.duration_min
                elif last_fired_day.get(i) != day_index and _in_daily_window(
                    time_of_day, ev.time_of_day_min, dt_min
                ):
                    carbs += ev.carbs_g
                    last_fired_day[i] = day_index

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

            adapter.step(state, params, carbs, basal, exercise_pct, hr_bpm, sim_clock_min, dt_min)
            glucose = adapter.glucose_mg_dl(state, params)

            # For the food/exercise graph: rate-fed models already carry a g/min rate;
            # impulse-fed models deliver the whole meal on one tick, so express that
            # tick's delivery as an equivalent rate too, for a comparable plot.
            carbs_rate = carbs if adapter.rate_fed else (carbs / dt_min if carbs > 0.0 else 0.0)

            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.expected_reading.emit(timestamp, glucose, carbs_rate, exercise_pct)
            print(
                f"[engine] t_sim={sim_clock_min:.2f}min dt={dt_min:.4f} fast={self._fast_mode} "
                f"glucose={glucose:.2f} carbs={carbs_rate:.3f} ex={exercise_pct:.1f}"
            )

            sim_clock_min += dt_min
            elapsed = time.monotonic() - tick_start
            self.msleep(max(0, int(1000 - elapsed * 1000)))
