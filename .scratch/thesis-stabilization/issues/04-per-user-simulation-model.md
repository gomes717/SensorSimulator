# One simulation model per user / slot (not one shared)

Status: done (2026-09-08, commits 1f06d1c + ec6c28e)
Track: A
Phase: 1
Blocked by: 01

## Outcome

- `models/engine.py` `EnginePool(QObject)` — `dict[slot, SimulationEngine]`,
  re-emits each tick tagged with its slot, one shared speed, fan-out
  pause/resume/stop, `add_instant_*` routed by slot (None = all). Rebuilt
  wholesale on any change.
- `main_window`: `self._engine` -> `self._engines = EnginePool`.
  `_engine_slots()` = per-slot when the layout has assignments and not Model
  Only, else `{0: active_person}`. `_on_expected_reading(slot, ...)` files
  per-slot into `_history[user_id]["ex_g*"]` keyed by the same `device_label`
  the received stream uses; single-sensor / Model Only keep the old global
  `_expected_*`. Layout change rebuilds the pool.
- Slot->name relabel: the instant-event dialogs' "Slot: 0/1/2/3" combo is now
  "Target: All sensors / Sensor N — <person>".
- Tests: `test_engine_pool.py`, `test_multi_slot_engines.py` (88 total).
- Hardware: a 4-person layout + Start builds 4 engines with 4 distinct
  trajectories, each into its own `P{i} — Sensor {i+1}` bucket; slot 0's
  received = expected minus the firmware's per-slot Ideal offset.

## Not done here

The `e2e_4sensor.py` "per-slot expected **vs received**" case waits on issue 07
— on Windows this process only receives slot 0's stream (the WinRT 4-way
notify-subscription drop). The per-slot *expected* side is pinned by the two
pytest files above.

---

## Problem

`docs/TODO.md`: "hoje roda um único modelo para todos os usuários; deve haver um
modelo por usuário." `MainWindow` creates exactly one `self._engine` bound to
`self._active_person` (`main_window.py:1133`); `_on_expected_reading` appends to
a single `_expected_x/_y` pair with no per-user dimension
(`main_window.py:1224-1226`). Received data *is* bucketed per `user_id` in
`_history` (`main_window.py:155`, `:1069`). So on a 4-slot board the firmware
runs 4 independent models but the app's "expected" overlay reflects only the one
person picked in the Configuration combo — the expected-vs-received cross-check
(`docs/ARCHITECTURE.md:248-264`, the core correctness argument) is only valid for
one slot at a time.

## What to do

- Replace `self._engine` with `dict[slot, driver]` (the extracted step from 01
  behind a thin `QThread` driver). One driver per occupied slot.
- Single-sensor / no-board case routes through the same dict with one entry — one
  code path.
- Rebuild all drivers wholesale on any board-layout change (the current
  `_restart_engine` pattern, for N).
- Clock, speed and run-state are shared across all drivers; `pause` / `resume` /
  `stop` fan out to every driver. Every expected line stays anchored to the one
  `self._graph_t0`.
- Each driver emits into its slot's `_history` bucket, so switching the tree row
  shows that slot's expected + received together.

## Fold in (Track B item, same files)

"Tirar o slot das janelas de configuração — usar o nome do usuário ou qual
sensor se quer configurar." Do the slot→name relabel in the per-config windows
here while this code is open.

## Done when

- 4-slot layout shows 4 distinct expected lines, each matching its slot's
  received line under the noise-only tolerance.
- A pytest drives 2+ drivers with distinct profiles through the shared clock and
  asserts independent trajectories.
- `scripts/e2e_4sensor.py` gains a per-slot expected-vs-received case.

## Refs

Architecture review §2 ("one model for all users vs one per user").
