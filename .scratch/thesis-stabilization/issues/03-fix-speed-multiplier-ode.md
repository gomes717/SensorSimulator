# Fix the speed multiplier breaking ODE integration at high speed

Status: done (2026-09-08, commit 8873dfd)
Track: A
Phase: 1
Blocked by: 01

## Outcome

The firmware already sub-steps (`MODEL_SUBSTEP_MAX_MIN = 1.0` in
`model_thread.c`). What was missing: the app's `ModelStepper` used the full
`dt_min`, so at high speed the "expected" line diverged from "received"
(UVA/Padova collapses to 0, Cambridge/Deichmann overshoot ~30-40 mg/dL).

- `ModelStepper._tick_model` runs the identical loop: `nsub = ceil(dt_min /
  MODEL_SUBSTEP_MAX_MIN)`, impulse carbs on sub-step 0 only, rate carbs +
  exercise every sub-step. Same constant (1.0), pinned by a test.
- `tests/test_engine_step.py` +6: x1000 physiological for all 4 models; x10-x300
  track x1 to <5 mg/dL; no-cap UVA/Padova -> 0.
- `docs/MODELS.md` documents it.
- Hardware x300 + 80 g meal: board and app both peak ~405 mg/dL, same shape.

## Deferred (firmware sub-item)

A Speed write still goes through `apply_config_locked` (resets `sim_clock`,
clears instant events). Making speed a live "hot" scalar needs a firmware
change on both `config_service.c` and the app's `_on_speed_changed` (which
calls `_restart_engine`). Not done � noted in `docs/TODO.md`.

---

## Problem

`docs/TODO.md`: "Arrumar o multiplicador de tempo — em velocidades altas está
quebrando a integração da EDO." Both the app (`engine.py:339`) and the firmware
(`model_thread.c`) use `dt_min = (1/60) * speed_mult` directly as the Euler
step. At x1000 that is a ~16.7-minute step, well past the stability limit of
explicit forward Euler for these models — which directly contradicts the written
methodological claim in `docs/MODELS.md:74-104` that explicit Euler is
numerically stable at the `dt` values used.

Speed is documented as a "nice capability / demo + test aid", not tied to a core
claim — but the ODE breakage undermines a *chapter*, so it is Track A.

## What to do

Cap the integration sub-step at a stability ceiling (e.g. `DT_MAX_MIN`) and loop
the integrator internally that many times per 1 Hz tick, so `speed_mult` only
changes how much simulated time a tick represents, never the numerical step
size. Mirror the same cap in `engine.py` and `model_thread.c` so the "expected"
and "received" lines stay identical.

Decide and document `DT_MAX_MIN` per model (tie it to the stated Euler-stability
argument in `docs/MODELS.md`).

## Done when

- A pytest runs each model at x1, x60, x1000 and asserts the trajectory matches
  a fine-step reference within a documented tolerance (no divergence, no NaN,
  non-negativity clamp not doing the work).
- `docs/MODELS.md` describes the sub-stepping; the `docs/TODO.md` bug line is
  removed.
- E2E S5 re-checked on hardware.

## Note

Separate known issue — a Speed write currently runs the firmware's full
`apply_config_locked()` (resets `sim_clock_min`, clears instant-event arrays;
`docs/E2E_TEST_PLAN.md:391-396`). Not committed here; fix only if cheap while in
this code.
