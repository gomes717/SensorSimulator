# Physiological Models & Sensor Noise

This is the narrative companion to
[`cgmsim/FORMULAS.md`](../cgmsim/FORMULAS.md), which has the full ODE
systems and complete parameter-by-parameter tables for all four
physiological models and three sensor noise models. This document instead
answers: *why these specific models, what numerical method turns their
equations into a running simulation, and how does the MCU's real-time tick
map onto the math.* Full academic citations for every model/sensor are in
FORMULAS.md's **References** section.

## 1. Why four different models

Each model represents a different, genuine trade-off in the glucose-insulin
modeling literature, and the project runs all four so a user can pick the
right one for what they're testing:

| Model | States | Distinguishing feature | Good for |
|---|---|---|---|
| **Cambridge (Hovorka)** | 10 | Compact, widely used in closed-loop/artificial-pancreas control research | General-purpose T1D simulation, fast to reason about |
| **UVA/Padova T1DMS** | 13 (core) | FDA-accepted as a substitute for animal trials in artificial-pancreas research; the most physiologically detailed of the four (explicit gastric emptying kinetics, hepatic/peripheral insulin split, renal glucose clearance) | High-fidelity meal response |
| **Roy & Parker** | 8 | Extends Bergman's classic minimal model with an explicit **exercise** term (three parallel gain/decay pairs for hepatic production, uptake, and insulin clearance) | Studying exercise effects with a simple, fast model |
| **Deichmann** | 9 | Also exercise-focused, but drives exercise intensity from **continuous heart rate** rather than a flat "exercise level," and splits the effect into an acute (during) and sensitizing (post-exercise, hours-long) component | Modeling the well-known delayed post-exercise hypoglycemia risk |

Cambridge and UVA/Padova have no exercise term at all (food only); Roy/Parker
and Deichmann are the two exercise-capable models — this is why the app's
`exercise_config_window.py` and the Exercise Instant dialog quietly have no
effect if the active person is running Cambridge or UVA/Padova (see
`models/engine.py`'s per-model adapter table).

## 2. Common contract

Despite very different internal state, every model exposes the same shape
to the rest of the codebase (`models/engine.py`'s `_ModelAdapter`, mirrored
by the firmware's `switch (active_cfg.model_id)` in `model_thread.c`):

- `default_params()` — a starting parameter dict, overridden per-`PersonProfile`
  by whatever the user configured (`api/protocol.py`'s wire layout, §3 below).
- `init_state(params)` — computes a **steady-state initial condition** at
  the target fasting glucose `Gpeq` (or `Gb` for Deichmann — see
  FORMULAS.md §4's parameter reference), rather than starting from an
  arbitrary point and letting the ODE settle. This matters for a live demo:
  starting off-steady-state would show a visible drift toward equilibrium
  in the first few simulated minutes even with no food/exercise input,
  which would look like a bug.
- `step(state, params, carbs_g_per_min, iir, exercise_pct, hr_bpm, t_sim_min, dt_min)`
  — advances the state by one tick (§4 below).
- `glucose_mg_dl(state, params)` — the one observable output, extracted
  from whichever state variable represents plasma (or subcutaneous, for
  UVA/Padova's `Gs`) glucose.
- `basal_u_per_h(params)` — the constant basal insulin infusion that holds
  the model at its steady state indefinitely (used because this project
  models no pump/bolus dosing — see FORMULAS.md's "Insulin/basal" note in
  `PROTOCOL_SPEC.md` §4).

This uniform contract is what lets `engine.py` (Python) and `model_thread.c`
(firmware) each be a single generic tick loop with a `switch`/dispatch-table
on `model_id`, rather than four separate simulation loops.

## 3. Where the parameters come from

Every parameter in FORMULAS.md's tables is a **wire-format field** — the
BLE Person/Sensor Config characteristics carry exactly these values, in
exactly this order, as `float32` (`api/protocol.py`'s `_MODEL_PARAM_NAMES`/
`_SENSOR_PARAM_NAMES`, matching each model's C `Params` struct field order
byte-for-byte — see `model_thread.c`'s `BUILD_ASSERT`s and
[`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md) §2). Nothing is hardcoded
differently on the two sides: a `PersonProfile` saved in the app's
`data/profiles.json`, sent to the board, and read back should decode to the
identical parameter dict it started as — this round-trip is exercised by
every config window's Read from Board button.

## 4. Numerical method: explicit Euler

All four models (and every sensor noise model) use **explicit (forward)
Euler integration** — the simplest possible fixed-step ODE method:

```
state[t + dt] = state[t] + dt · f(state[t], params, inputs)
```

computed once per tick, for every state variable, with a **non-negativity
clamp** on the result (`models/cambridge.py`'s `step()`:
`s.Q1 = max(0.0, s.Q1 + dQ1 * dt_min)`, identically in the C firmware
version) — a physical quantity like a glucose mass or insulin concentration
cannot legitimately go negative, but a large enough Euler step combined with
a stiff enough derivative term can numerically overshoot past zero; clamping
is the cheap embedded-friendly guard against that, in place of a more
expensive adaptive-step or implicit solver.

**Why explicit Euler instead of, say, Runge-Kutta 4:** this is an embedded
MCU running two other things (BLE stack, flash I/O) on a shared CPU, not a
dedicated numerics workstation — Euler is O(1) derivative evaluations per
step versus RK4's 4×, and at the `dt_min` values actually used here (down
to 1/60 minute = 1 second, see §5) all four models stay numerically stable:
their fastest time constants (the quickest-reacting state variables, e.g.
Cambridge's `x1`/`x2`/`x3` insulin action states or Deichmann's `Y`) are
still tens of times slower than a 1-second step, which is the informal
stability condition for explicit Euler on a linear(ized) system (step size
well under the fastest eigenvalue's time constant). The same method, same
parameters, same code path (just C vs. Python) runs on both the firmware
and in the app's `SimulationEngine` — using anything fancier on one side
and not the other would itself introduce a source of "expected vs.
received" divergence that has nothing to do with sensor noise.

## 5. The timestep, from the MCU up

The physical tick rate is fixed in the firmware:
`model_thread`'s loop calls `k_sleep(K_SECONDS(1))` — **exactly one
simulation tick per real-world second**, unconditionally. What changes with
the user-selected mode is `dt_min`, i.e. how much *simulated* time that
one real second of computation represents (`model_thread.c`):

```c
double dt_min = (active_cfg.mode == SIM_MODE_FAST) ? 1.0 : (1.0 / 60.0);
```

| Mode | `dt_min` per tick | Real time → simulated time |
|---|---|---|
| Normal | 1/60 min (1 s) | 1:1 — a 1-hour session takes 1 real hour |
| Fast | 1 min | 60:1 — a simulated day (1440 min) takes 24 real minutes |

`sim_clock_min` (`model_thread.c`) is simply `Σ dt_min` since the last reset
— a free-running `double`, not derived from a wall-clock timestamp, which
is what makes Pause genuinely freeze simulated time rather than just
stop plotting it (`model_thread_set_run_state(SIM_RUN_PAUSED)` skips calling
`model_tick()` entirely; no ticks happen, so `sim_clock_min` doesn't
advance, and resuming continues exactly where it left off). The recurring
food/exercise schedule's daily-window check
(`fmod(sim_clock_min, 1440.0)`, PROTOCOL_SPEC.md §2) and impulse-fed
models' once-per-simulated-day firing both key off this same clock.

`models/engine.py`'s `SimulationEngine` mirrors this exactly on the Python
side — `self.msleep(...)` targets one tick per wall-clock second (adjusting
for however long the tick's own computation took, so a slow tick doesn't
compound into perpetual drift), with the identical `dt_min` derivation. Two
independent clocks computing the same thing, not one shared clock — see
[`ARCHITECTURE.md`](ARCHITECTURE.md) §5 for why.

## 6. Food and exercise delivery — rate-fed vs. impulse-fed

A tick's `carbs_g_per_min` input to `step()` is computed differently
depending on the model (`models/engine.py`'s `adapter.rate_fed`,
`model_thread.c`'s `model_is_rate_fed()`):

- **Rate-fed** (Cambridge, UVA/Padova): while inside a meal's
  `[time_of_day_min, time_of_day_min + duration_min)` window, every tick
  contributes `carbs_g / duration_min` — a constant rate for the whole
  window. This matches how these two models' own gut-absorption
  compartments (`D1`/`D2` for Cambridge; `Qsto1`/`Qsto2`/`Qgut` for
  UVA/Padova) already do the time-spreading internally — feeding them a
  rate rather than a pulse avoids double-modeling the same physiology.
- **Impulse-fed** (Roy&Parker, Deichmann): the *entire* `carbs_g` is
  delivered on a single tick — the first tick the simulated clock reaches
  `time_of_day_min` (tracked per event slot so it fires exactly once per
  simulated day, or exactly once for an instant event — see
  [`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md)'s "Instant food/exercise
  events" section); `duration_min` is unused for delivery itself. These two
  models' own trapezoidal (Roy&Parker) or two-compartment `D1`/`D2`
  (Deichmann) meal absorption terms are what spread that single pulse out
  over time inside the ODE.

Exercise intensity works the same way structurally but has no "spread"
question — it's just "on at `intensity_pct` for the window, off outside
it," summed for **recurring + instant** events by taking whichever source
gives the higher value at that tick (an active instant bout can only raise
exercise intensity above the recurring schedule, never suppress it — see
`model_thread.c`'s instant-event tick integration, or
[`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md)'s instant-events section for the
full mechanism, including why instant events don't reset the simulation the
way every other config write does).

## 7. Sensor noise — deliberately firmware-only

The three sensor models (Ideal, Breton & Kovatchev 2008, Facchinetti et al.
2014 — FORMULAS.md §6) are **only implemented in the firmware**
(`cgmsim_sensors.c`) — there is no Python port. This is intentional, not an
omission: the app's `SimulationEngine` produces the "expected" line as a
noiseless ground truth to compare *against*, so the only place sensor noise
should exist is the "received" line coming from the real board. If the app
also modeled sensor noise locally, the two lines would each carry their own
independent random noise realization and never usefully overlay — the
comparison would tell you nothing about whether the board's simulation is
correct. `SensorProfile`s still round-trip through the app (saved, sent,
read back) exactly like `PersonProfile`s; they're just never *evaluated* on
the Python side.

**No sensor throttles its own output rate** (removed 2026-08-18, was a
`sampling_time_min` field defaulting to 5 min) — every sensor now emits a
fresh reading every `model_thread` tick, unconditionally. How often the app
actually observes a *new* value is purely a function of transport cadence
(`comm_thread`'s poll interval and the standard CGMS notify timer — see
[`FIRMWARE.md`](FIRMWARE.md) §4.3), not something the sensor model itself
decides. See [`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md)'s Sensor config
section for why the two were deliberately decoupled.
