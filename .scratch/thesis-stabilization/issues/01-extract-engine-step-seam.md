# Extract the engine per-tick logic into a pure step

Status: ready
Track: A
Phase: 1
Blocked by: —

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
