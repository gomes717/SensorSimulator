# Firmware: G6 GATT, advertisement and auth

Status: done 2026-10-18, hardware-verified (right ID authenticates, wrong ID and control-before-auth refused). ADR 0007 not written yet
Type: task
Blocked by: 02, 04

## Build

- Replace `dexcom_service.c` with the real layout: service `…3532`, Control `…3534` (write +
  indicate), Authentication `…3535` (write + read + indicate), Backfill `…3536` (notify), ExtraData
  `…3538` (notify), Device Information. **No encryption permission on any of them.** The G6 module
  `dexcom_g6.c` (ticket 02) does the protocol; this file is GATT glue + the per-connection state.
- Advertisement on identity 0 in Dexcom mode: `0xFEBC` + whatever ticket 01 found necessary; name
  `Dexcom` + ID chars 4–5 (built from the stored ID, rebuilt when it changes).
- Per-connection auth state (idle → token → challenge sent → authenticated). Control writes before
  authentication fail with ATT error 8, as on a real G6; a wrong hash → `AuthStatusRx authenticated=0`
  and no data. Report `bonded=1`.
- AES through PSA (`PSA_ALG_ECB_NO_PADDING`); measure flash/RAM; fall back to a small AES-128 file
  if it is costly.
- Unknown opcodes → `dexcom: unknown op 0x.. len ..` on the console, ignored.
- ADR `docs/adr/0007-board-modes-multi-and-dexcom-g6.md` (decisions 1–3 of the spec).

## Done when (hardware-verified)

- `scripts/hw_g6_client.py` authenticates against the board with the right ID and is **refused** with a
  wrong one; xDrip's hash check passes (our `tokenHash` equals what it computes).
- Multi mode and the Multi-mode E2E suites unaffected; Dexcom-mode identity 1 still serves the app.
- Serial log of one full handshake pasted into the ticket.
