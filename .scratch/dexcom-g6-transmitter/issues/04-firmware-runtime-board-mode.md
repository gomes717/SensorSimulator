# Firmware: runtime board mode (Multi ↔ Dexcom identity layout)

Status: done 2026-10-18, hardware-verified (scripts/hw_dexcom_mode.py 27/27). Not yet checked on hardware: the 3 s button hold and the LEDs
Type: task
Blocked by: —

Goal: the mode switch, with **no G6 crypto yet**. In Dexcom mode identity 0 advertises as the
transmitter (name + `0xFEBC`, placeholder GATT is fine), identity 1 as `Nordic Glucose Sensor 1`,
identity 2 silent. Everything later (05, 06) plugs into this.

## Build

- `sim_config`: version 7, add `char dexcom_tx_id[7]` (default `8SIM00`); loading a v6 image still
  works. Stop forcing `comm_profile` to SIG when `sensor_count > 1`. Effective `sensor_count` is
  derived from the mode at load and on switch (`Dexcom → 1`, `Multi → CONFIG_APP_SENSOR_COUNT`);
  slots 1–2 data stays in flash untouched.
- New characteristic *Dexcom transmitter ID* (read + write, 6 bytes `[0-9A-Z]`, anything else refused
  with ATT `0x0D`/`0x80`), registered **last** in the GATT table; documented in `PROTOCOL_SPEC.md`
  (reword *Comm profile* as **Board mode**, same UUID and wire).
- `main.c`: drop the `#if N_SENSORS == 1` around the profile code; per-identity `identity_active(i)`
  and `identity_name(i)` (table in `spec.md`); `arm_adv`, `advertising_work_handler` and
  `disconnected()` honour them; the adv-data builder picks Dexcom / SIG per identity.
- Switching (`main_apply_comm_profile` generalised, also called on an ID write in Dexcom mode):
  persist → stop all adv sets → disconnect all links → set mode → reset sim clock + instant events →
  force speed ×1 when entering Dexcom → re-arm per mode.
- **Single entry point `board_mode_set(mode)`** used by the BLE write, the button and boot, so all
  paths behave identically.
- **Button**: `dk_buttons_init()` (already enabled via `CONFIG_DK_LIBRARY`); Button 1 held 3 s toggles
  the mode (a delayable work armed on press, cancelled on release; short presses ignored; both mode
  LEDs flash fast while armed). Works in CGMS-only mode and with no BLE client.
- **LEDs**: `DK_LED2` blinks (1 Hz) in Multi mode, `DK_LED3` blinks in Dexcom mode, the other off;
  both steady ~1 s during a switch. `DK_LED1` keeps its current connected / not-connected behaviour.
  Blink is driven by the mode variable, not by advertising state. LED/button choices are `#define`s.
- `disconnected()`: **exempt identity 0 in Dexcom mode from the short-link backoff** (a G6 poll is a
  few seconds long by design).
- `comm_thread`: the push loop already uses the runtime count; make `dexcom` = mode only (not
  `count == 1`) and leave the Dexcom branch a stub until 06 (glucose still reaches the app through
  SIG CGMS on identity 1 meanwhile).

## Done when (hardware-verified, app-free: nRF Connect / the harness scripts)

- Multi→Dexcom→Multi: after each switch exactly the expected identities advertise with the expected
  names; the previous links are dropped; a power-cycle boots into the saved mode.
- Switch by **BLE write** and by **3 s button hold**: both end in the same state; a 1 s press does
  nothing; the 2nd LED blinks in Multi, the 3rd in Dexcom, never both, and both are steady during the
  switch; the LEDs are right after a power-cycle into each saved mode.
- The button still switches while the board is in CGMS-only mode (BLE writes are refused there).
- Slots 1–2 config is identical after a round trip; slot 0 runs in both modes.
- A connection to identity 1 in Dexcom mode receives CGMS instance 0's notifications.
- Multi mode behaves exactly as before (existing E2E suites green, 3 sensors streaming).
- Serial log of one full round trip pasted into the ticket.
