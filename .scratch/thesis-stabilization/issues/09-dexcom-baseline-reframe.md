# Dexcom protocol — honest baseline reframe

Status: ready
Track: A
Phase: 1
Blocked by: —

## Problem

`docs/TODO.md`: "Validar o protocolo Dexcom." The plan was to validate via a
third-party app "confirmed to work with Dexcom". The sub-agent review found this
does not work as built:

- The board's stream is a **project-invented** format — opcode `0x4E`, 14 bytes
  `<BBIIHBb>` (`firmware/peripheral_cgms/src/dexcom_service.c:70-98`,
  `src/api/protocol.py:125-159`) — not the real G6/G7 EGV layout.
- No auth: control writes are ACKed and ignored
  (`dexcom_service.c:37-39`, `PROTOCOL_SPEC.md:407`).
- Real transmitters require J-PAKE (G7) / AES (G6) "before any glucose flows"
  (`docs/BLE_PAYLOAD_VALIDATION.md:94`). xDrip+/DiaBLE/Loop are cited only as
  *sources of protocol info*, never as confirmed interop targets
  (`docs/architecture_flows.tex:301-310`).

So a known-good Dexcom app will attempt auth and fail, and wouldn't parse this
payload anyway.

## What to do (baseline — always ships)

- Reframe the claim: the board streams "a second, non-SIG proprietary wire
  format" demonstrating the app is **protocol-agnostic** — not "compatible with
  real Dexcom tooling."
- Validation = the project's own decoder round-trips it (E2E **S16-01**, already
  green) + a documented byte-layout table in `PROTOCOL_SPEC.md` /
  `docs/BLE_PAYLOAD_VALIDATION.md`.
- Revise `docs/architecture_flows.tex` (§ around :292-328) to drop any
  implication of real-Dexcom compatibility; keep the subsection in the main body
  under the narrower claim.
- `comm_profile` stays single-sensor-only (`PROTOCOL_SPEC.md:425-427`).

## Done when

- S16-01 stays green and is in the `scripts/e2e.py` subset.
- The byte-layout table is in the docs and the `.tex` no longer overclaims.
- `docs/TODO.md` "Validar o protocolo Dexcom" line points at issue 12 for the
  stretch.
