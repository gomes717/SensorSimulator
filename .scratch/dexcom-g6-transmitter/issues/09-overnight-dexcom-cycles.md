# Overnight headless test: Dexcom and mode-switch cycles

Status: standalone version done 2026-10-18 (scripts/overnight_dexcom.py + overnight_dexcom_analysis.py); integration into overnight_users.py (Board-mode UI flows) still open
Type: task
Blocked by: 06, 07 (the G6 client from 03)

Extend `scripts/overnight_users.py` (the real `MainWindow`, offscreen, private data folder, answers
its own modals, board on COM10 — see `docs/E2E_TEST_PLAN.md` §10c) so one unattended night covers
both board modes. Everything the PC app does in Dexcom mode goes through the app's own UI flows
(Board-mode *Apply*), not around them.

## Cycle kinds

Selected by `--dexcom {off,on,only}` (default `on` once this lands) and a deterministic pattern per
`--seed`, e.g. *Multi-odd, Multi-even, Dexcom-accelerated, Multi-odd, Multi-even, Dexcom-real-time*,
repeating. The harness checks the board's mode at start and **ends every cycle and the night in
Multi mode** (cleanup switches back; a crash leaves a note in SUMMARY.md saying which mode the
board was left in).

**Multi-odd / Multi-even** — unchanged (the regression guard that Multi mode still behaves).

**Dexcom-accelerated** (x60, ~25 min = 25 sim hours ≈ 300 readings):
- *setup*: from Multi, switch to Dexcom with the app's Board-mode flow; the app drops and reconnects;
  assert: mode read back = Dexcom, exactly **one** sensor tab, a BLE scan shows `Dexcom…` and
  `Nordic Glucose Sensor 1` and **not** Sensor 2/3, the Bluetooth window offers no Connect on the
  `Dexcom…` device; slot 0 gets a user (model, then in the odd Dexcom cycle a CSV user) via *Send to…*.
- *run*: Start; the Python G6 client (xDrip's sequence, a second bleak link on the same adapter)
  authenticates and polls every grid point while the app stays connected on identity 1; frontend
  work as in the Multi cycle (open Configuration, avatar change, close/reopen the tab, Insert
  Food/Exercise/PISA); the G6 client is dropped and reconnected once, and the app's tab once.
- *scored* (pure functions in `overnight_analysis.py`, unit-tested): `seq` rises by exactly 1 per
  300 sim-s and never repeats or skips; `ts` steps 300 s; glucose within 40–400; each reading equals
  the app's SIG value for that `seq` (same reading, two outputs); board reading vs host model at
  that sim time within the existing parity tolerance; the events moved the next readings; for a
  CSV user the 289th reading equals the 1st (24 h loop on the 5-minute grid); a **wrong-ID** auth
  attempt is refused once per cycle and returns no data; the serial log has no `unknown op`, no
  assert/fault, no unexpected reset.
- *after*: Pause holds `seq`, Resume continues it, Stop resets `seq`/`ts`, Start streams again;
  switch back to Multi: three identities advertise, the three slots' config (slots 1–2 hashed
  before the Dexcom block) is unchanged, three tabs return.

**Dexcom-real-time** (x1, `--realtime-min`, default 90 min ≈ 18 readings): the G6 client behaves like
xDrip — sleeps to *grid point − lead*, scans, connects, reads, disconnects — while the app stays
connected the whole time.
- scored: every window hit (a read per grid point, latency from `T_k` recorded), no stale/duplicate
  `seq` on a first read of a window, no skipped `seq`; **outside the window the board is not
  connectable** (a scan between `T_k+60 s` and `T_{k+1}−10 s` does not see `Dexcom…`, inside it does);
  the app's link never dropped; app plot vs G6 readings identical; parity at x1; link-drop count.
- A mid-block board-side restart (reset) is a failure with the serial excerpt attached.

## Plumbing

- New `scripts/overnight_dexcom.py` (cycle functions) + G6 scoring added to `overnight_analysis.py`;
  `overnight_users.py` only picks the cycle kind and calls in. `Flows` gains `set_board_mode(mode)`
  (drives the Configuration window, answers the confirmation) and `wait_board_mode(mode)`.
- Cycle reports gain a *G6* section (readings table, latency histogram, the window scan results);
  SUMMARY.md lists the mode per cycle.
- `--smoke` gets two short variants: Dexcom-accelerated at x250 (~6 min) and Dexcom-real-time for 12
  min; `--no-board` still runs only the frontend flows (the Board-mode UI against a fake board).
- Never run alongside the user's app or another harness (COM10, one adapter) — same rule as today.

## Done when

- `--smoke --dexcom only` passes on the board; one full night with `--dexcom on` produces a SUMMARY
  with Multi, Dexcom-accelerated and Dexcom-real-time cycles, and the board is back in Multi at the end.
- `docs/E2E_TEST_PLAN.md` §10c and the `overnight_users.py` docstring describe the new cycles; the
  pure scoring has unit tests (gaps, duplicates, grid drift, window hit/miss).
