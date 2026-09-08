# Fix the speed multiplier breaking ODE integration at high speed

Status: blocked (01)
Track: A
Phase: 1
Blocked by: 01

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
