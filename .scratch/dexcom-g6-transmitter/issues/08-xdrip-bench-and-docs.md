# xDrip on Android + thesis text

Status: blocked on the Android phone; PROTOCOL_SPEC.md and E2E_TEST_PLAN.md updated, the .tex and BLE_PAYLOAD_VALIDATION wording still to do after the bench
Type: task
Blocked by: 06 (07 for the combined demo)

## Pre-flight (manual — not automated by the overnight test)

- Hold Button 1 for 3 s: the board toggles mode; a short press does nothing; during the hold both
  mode LEDs flash, afterwards the 2nd LED blinks in Multi and the 3rd in Dexcom; works while the app
  is connected and while CGMS-only is on; the app reports "board switched to …" and follows.

## Bench checklist

1. App: Board mode → Dexcom G6, note the transmitter ID, speed ×1. Board restarts; identity 0
   advertises `Dexcom…`.
2. xDrip: Hardware Source → *Dexcom G5/G6 native* ("Ob1"), enter the ID, enable **use transmitter
   algorithm**, G6 mode; start a "sensor" if it insists.
3. Watch the board's serial console while xDrip scans, authenticates and reads. With a deliberately
   **wrong** ID xDrip must fail (proves the secret matters).
4. A reading appears in xDrip and equals the app's value for the same sim time.
5. Leave it ≥ 1 h: one new reading every 5 minutes, no lag cycles (the connect-window design), no
   disconnect storm, `seq` advancing. Screenshot xDrip's graph beside the app's.
6. With the PyQt app connected at the same time: Insert Food in the app moves xDrip's **next**
   reading; the app's graph shows the same 5-minute points.
7. Switch the board back to Multi from the app: xDrip stops finding it, the 3 sensors stream again.

## Docs

- `docs/architecture_flows.tex` §≈292–328: replace the "engenharia reversa fora do escopo" /
  "esboço mínimo" wording with what is now true (G6 AES auth, real EGV layout, 5-minute cadence,
  validated against xDrip); keep the claim narrow.
- `docs/BLE_PAYLOAD_VALIDATION.md` §2, `PROTOCOL_SPEC.md`, README: board modes and the byte tables.
- Close issue 12 pointing here; add the evidence (log excerpt + screenshots) to `docs/results/`.

## Done when

xDrip shows live glucose from the board for an hour. If that is not true by **2026-10-30**, ship
issue 09's baseline wording and move the rest to future work (spec guard rail).
