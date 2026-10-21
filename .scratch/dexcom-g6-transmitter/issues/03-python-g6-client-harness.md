# Python G6 client (hardware test harness + "G6 check")

Status: done 2026-10-18 (src/services/dexcom_g6_client.py, 7 tests against the in-process transmitter; used by scripts/hw_dexcom_mode.py and overnight_dexcom.py)
Type: task
Blocked by: 02

## Build

`src/services/dexcom_g6_client.py` (bleak): connect, enable `…3538` notifications and `…3535` / `…3534`
indications, run the **same sequence xDrip runs** (auth request → verify the board's token hash →
answer the challenge → read `AuthStatusRx` → `EGlucoseTx` → decode). Pure protocol logic comes from
ticket 02; this file is only I/O.

`scripts/hw_g6_client.py`: command line (`--id 8SIM00 --address … --polls 5 --wait-window`), prints
decoded readings (seq, ts, glucose, trend), exits non-zero on any protocol failure. `--wait-window`
polls on the 5-minute grid like xDrip does, so the connect-window logic can be exercised without a
phone. Follows `scripts/hw_common.py` conventions.

The app does **not** use it to display Dexcom mode (it reads SIG CGMS on identity 1, see spec); it is
reachable from the app only as an optional "Check G6 link" action if time allows.

## Done when

- Unit tests drive the client against an in-process fake G6 peripheral: success, wrong ID ("board
  failed token verification"), bad CRC, disconnect mid-handshake, stale `seq`.
- Handles Windows' indication quirks (subscribe completes before the first write).
