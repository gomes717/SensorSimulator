# App: Board-mode UI, transmitter ID, slot count

Status: mostly done 2026-10-18 (Board group, tracker, Bluetooth window rescan/reconnect, G6 device not connectable); the app-side flow is not yet exercised against the real board, E2E S16 moved to hw_dexcom_mode.py
Type: task
Blocked by: 04 (the ID field also needs 05)

## Build

- Configuration window → **Board** group: mode choice ("3 sensors · standard BLE, no pairing" /
  "1 sensor · Dexcom G6 for xDrip"), transmitter-ID field (shown and validated `[0-9A-Z]{6}` only in
  Dexcom mode), *Apply* with a one-line confirmation (the board restarts, the simulation resets).
  Replaces the removed Comm-profile combo; uses `protocol.encode_comm_profile` and a new
  encode/decode pair for the ID.
- The mode can also change **without the app** (3 s button hold on the board): when the app
  reconnects it reads the mode back and follows it; if a link drops with the board's mode having
  changed, the status line says "board switched to Dexcom/Multi mode" instead of a generic disconnect.
- On a mode write the app shows "board restarting…" and uses the existing reconnect; after reconnect
  it reads the mode back and trusts the board (ADR 0002 "the board is the authority").
- **Slot count from the mode**: `board_layout.active_slots(mode)` (1 or 3); Dexcom mode shows one
  sensor tab and offers one slot in Users / *Send to* / board-layout push. `MAX_SLOTS` stays 3.
  Checked slot writes keep going through the existing clamp-aware helper.
- **Never connect to the transmitter identity**: a `Dexcom…` device in the Bluetooth window is
  labelled "G6 transmitter — for xDrip" with no Connect, and auto-reconnect / connect-all skip it
  (it would lock the phone out of the one G6 connection). Slot-0 sessions bind to
  "Nordic Glucose Sensor 1" exactly as in Multi.
- Speed control: in Dexcom mode shows "xDrip needs ×1" when the speed is not 1.
- Graph in Dexcom mode: board readings as 5-minute points over the continuous local model line; the
  parity metric compares each reading with the model at that sim time.
- Rewrite E2E **S16-01** (old 14-byte decoder is deleted along with `DEXCOM_*` UUIDs in
  `ble_uuids.py`, `protocol.decode_dexcom_glucose`, the `ble_session` branch); update
  `docs/E2E_TEST_PLAN.md`, `PROTOCOL_SPEC.md` (Board mode + ID char + G6 tables from the codec).

## Done when

- From the app: Multi → Dexcom → Multi on the real board, with the tabs / slot UI following, the
  setting surviving an app restart (it is read from the board, not stored in `data/`).
- All existing tests green; new tests cover ID validation, `active_slots`, the confirmation flow and
  the restart/reconnect state.
