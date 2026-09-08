# Extract the engine per-tick logic into a pure step

Status: done (2026-09-08, commit 64dd374)
Track: A
Phase: 1
Blocked by: —

## Outcome

- `ModelStepper` — pure, no QThread/sleep/wall-clock. `tick(dt_min, now_iso)`
  runs one loop-body iteration, advances `sim_clock_min`. Per-run state in
  `_CsvReplay` / `_ModelRun` holders. `add_instant_*` + `_pisa_factor` moved here.
- `SimulationEngine` reduced to a timing driver (`run()` = pace + `dt_min` +
  timestamp + `tick()` + `emit`). Public API unchanged; `main_window.py` untouched.
- `tests/test_engine_step.py` — 16 cases: raw-model equivalence (Cambridge +
  RoyParker, both adapter paths), exact PISA envelope, sim-clock, determinism,
  instant + scheduled food/exercise.
- Behaviour change: `[engine]` print drops `x{speed}` (kept `t_sim=...min` for
  `e2e.py`).
- Hardware: `scripts/ui_smoke.py` 6/6 PASS.

Unblocks 02 (PISA), 03 (speed/ODE), 04 (per-user model).

---


## Problem

`src/models/engine.py`'s real logic lives inside `_run_model` / `_run_csv`
(`engine.py:319-403`, `:256-317`) — private methods wrapped in
`while not self.isInterruptionRequested()` with `time.monotonic()`,
`self.msleep()`, `datetime.now(timezone.utc)` and bare `print()`. There is no
seam to step the simulation once or inject a clock. Only `_in_daily_window`
(`engine.py:65`) and `load_csv_window` (`engine.py:32`) are independently
callable. Everything downstream in Phase 1 (PISA, speed/ODE, per-user model)
needs this.

The logic trapped in the loop: daily-window recurrence, once-per-day impulse
firing via `last_fired_day` (`engine.py:331`, `:344-352`), instant food/exercise/
PISA decay + `delivered` bookkeeping (`engine.py:361-382`), `_pisa_factor`
(`engine.py:211-223`), impulse→equivalent-rate conversion for the plot
(`engine.py:392`), `dt_min = (1/60) * speed_mult` (`engine.py:339`).

## What to do

Extract a pure step: given `(model state, profile, dt_min, sim_clock_min,
pending instant-event lists, wall-clock timestamp)` return `(reading, new state,
new event lists)`. Do the same for the CSV replay path. `QThread.run()` keeps
only timing (`monotonic`/`msleep`) and the `expected_reading.emit`. No behaviour
change — the existing hardware E2E and the local "expected" line must be
byte-identical.

## Done when

A test advances the simulation N steps with an injected clock and asserts the
glucose / carbs-rate / exercise outputs, with no `QThread` and no sleeping.

## Refs

Architecture review §2 (engine testability friction).
