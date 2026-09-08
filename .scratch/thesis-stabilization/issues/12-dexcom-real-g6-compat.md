# Dexcom real G6 compatibility (STRETCH)

Status: blocked (Phase 1 complete + time remaining)
Track: A (stretch)
Phase: 2
Blocked by: 09, and all of Phase 1

## Goal

Make a known-good third-party Dexcom client (**xDrip+** on Android, "G6 native"
mode) read the board as a real transmitter, giving independent validation of the
stream.

## Scope

- **G6 / AES challenge** only — not J-PAKE/G7 (lower crypto surface;
  `docs/architecture_flows.tex:307-308` frames G6 as the simpler branch).
- Real EGV byte format reverse-engineered from xDrip+ / DiaBLE source (**you do
  the RE**). There is no official spec (`docs/architecture_flows.tex:310`).
- Firmware: implement the AES challenge/response on the control characteristic
  (`…3535`), emit the real EGV message layout on `…3538`, advert name pattern
  `DXCM..` with the pairing-code suffix
  (`docs/BLE_PAYLOAD_VALIDATION.md:91-99`).
- App decoder updated to parse the real layout alongside the current one.
- `comm_profile` stays single-sensor-only.
- Revise the `docs/architecture_flows.tex` "engenharia reversa … fora do escopo"
  wording — the RE is now in scope for this section.

## Guard rails (from the grill)

- **Drop-dead date: 2026-09-15 (defense).** If xDrip+ is not reading the board
  end-to-end by then, ship issue 09's baseline reframe (already done), move this
  to "future work", spend no more time.
- **Track A always wins.** The moment this contends for time with any Phase 1
  correctness item, this yields.
- Precondition **met**: user has an Android device (confirmed 2026-09-08).
  xDrip+ still to be installed / put in "G6 native" mode.

## Open question

Q24 "both" was ambiguous — this issue assumes **G6 only**. If you actually want
both G6 and G7, reopen the scope discussion (it roughly doubles the crypto work
and is hard to justify against "Track A always wins").

## Done when

xDrip+ on Android shows live glucose from the board, sourced through the AES
handshake + real EGV format, demonstrated on the bench.
