# One simulation model per user / slot (not one shared)

Status: blocked (01)
Track: A
Phase: 1
Blocked by: 01

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
