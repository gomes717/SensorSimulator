# Firmware: 5-minute readings, G6 replies, connect window

Status: done 2026-10-18, hardware-verified (5-minute grid, window shut at +75 s, second reading +300 s). Backfill / warm-up not done (optional)
Type: task
Blocked by: 05

## Build

- **Reading grid** (pure function + tests): at sim-time multiples of 300 s sample the model / CSV
  value, clamp 40–400, publish `{seq++, ts = sim s since mode start, glucose, trend, state 0x06}`;
  trend = model rate of change in tenths of mg/dL/min as int8 (`127` unknown); predicted glucose =
  glucose (or a 15-min linear extrapolation). First reading published immediately on mode entry / boot.
- **Both outputs from the same reading**: G6 replies (`4F`, `31`, `2F` best effort, `25`, version,
  battery, session, calibrate) *and* one SIG CGMS notification to the app per reading (not per tick) in
  Dexcom mode. Multi mode's per-tick push is untouched.
- **Connect window** on identity 0: connectable `[T_k, T_k + 30 s]`, stopped otherwise, extended while a
  client is connected and not yet idle; always open when the grid period is shorter than the window
  (speed > 1); first window opens at mode entry. Window length / lead are constants set from
  ticket 01's findings.
- **Speed**: entering Dexcom forces ×1; a later Speed write is allowed (app warns) and rescales the grid.
- Optional (cut first): RAM ring of the last 36 readings + backfill on `…3536`; optional warm-up
  state (`0x02`) toggle.
- Speed write / reset-sync behaviour unchanged (see `speed_write_resets_sim` memory).

## Done when (hardware-verified)

- The harness (`--wait-window`) reads for ≥ 1 sim-hour at ×1: one new `seq` per 5 minutes, repeated
  polls inside a window return the same `seq`, a poll outside the window cannot connect, and each
  value equals the app's expected-model value for that sim time (existing parity tolerance).
- With the app connected on identity 1 at the same time, both see the same readings.
- Serial log shows no unknown opcodes during a harness run.
