# Spike: what xDrip does before and after auth — and when

Status: source half done 2026-10-18; the real-xDrip log half is ticket 08's bench
Type: research
Blocked by: —

## Question

Which preconditions and timings does xDrip put on a transmitter beyond what `spec.md` lists?
Answer from source first (`services/Ob1G5CollectionService.java`, `g5model/Ob1G5StateMachine.java`,
`g5model/AdvMatcher.java`), then confirm with one real xDrip log if cheap.

## To settle

- Scan: filter by advertised **name** (`Dexcom` + which two chars of the ID?), by `0xFEBC`, or by the
  19-byte data `AdvMatcher` parses? Minimum advertisement that gets the board picked up.
- Bonding: does it insist when auth says `bonded=1`? What happens on ATT error 8 (insufficient
  authentication)?
- Command order on a **first** connection with "use transmitter algorithm": version request(s),
  battery, transmitter time, session start, backfill — mandatory vs only-when-queued.
- Does it need `…3536` (backfill) and `ServiceB` (`…4532`) to exist?
- Valid transmitter-ID format accepted by the settings screen.
- **Timing (drives the connect-window design):** after a successful read, when does it next scan —
  last receive + 5 min minus what lead? How long does it scan/retry? What does it do when it
  connects and gets the *same* `seq` again (stale reading)? How long does it wait for the indication
  (`doGetData` has a 10 s timeout)? How does it react to the transmitter dropping the link right after
  the reply?
- Does it tolerate a transmitter that is not connectable outside a ~30 s window (no advertising at all)?

## Answer

Status: **source half answered 2026-10-18**; the "one real xDrip log" half needs the phone and is
deferred to ticket 08's bench.

Read from `Ob1G5CollectionService` / `Ob1G5StateMachine` / `g5model/*`:

- **Scan** (Android ≥ 8): filter by device **name** `"Dexcom" + last two characters of the ID` when the
  ID is longer than 4 characters; otherwise by service `0xFEBC` (16-bit mask). Once it has seen the
  transmitter it filters by **MAC** with `CALLBACK_TYPE_FIRST_MATCH`. So: the name must be
  `Dexcom` + ID[4:6], and the `0xFEBC` UUID in the advertisement is a safe addition. `AdvMatcher`
  (19-byte data) is not used by the scan filter.
- **Scanning is persistent** (status screen warns at 5 / 10 min of scanning, i.e. it keeps scanning
  until the transmitter appears). → A transmitter that is only connectable in a window is safe: xDrip
  just connects when the advertisement shows up. Real wake lead stays unmeasured (bench).
- **Bonding:** auth `bonded` field is read as `== 1`; the code reacts to a `bondNow` plugin hook and an
  Android bond-state receiver. ATT error 8 on a Control write is treated as authorisation failure
  (`doGetData`). Not yet seen: whether `bonded=1` + no SMP is accepted → bench.
- **Data request** with "use transmitter algorithm": `EGlucoseTx` (0x4E, 3 bytes: op + CRC) on G6,
  `GlucoseTx` (0x30) on G5; the reply is awaited as an *indication* on Control with a 10 s timeout.
  Without that setting it sends `SensorTx` (0x2E). Notifications on ExtraData (`…3538`) are enabled
  before the Authentication indication → both characteristics must exist and accept a CCC write.
- **Other opcodes seen** (`g5model`): version 0x20 / 0x4A / 0x52 → reply 0x21 (≥ 18 bytes:
  status, 4+4 byte firmware strings, hw rev, 3-byte string, asic u16); battery 0x22 → 0x23 (≥ 10 bytes:
  status, voltA u16, voltB u16, [resist u16], runtime, temp); session start 0x26 → 0x27 (17 bytes: status,
  info, requestedStart, sessionStart, txTime); session stop 0x28 → 0x29 (17 bytes); transmitter time
  0x24 → 0x25 (≥ 10 bytes). Replies from `0x25` on are CRC-checked by xDrip; the auth ones are not.
- **ID format:** G6 = 6 characters (the code logs an error for any other length when building the key).
  The 4-character "short" ID is the G7/Dexcom One path — out of scope.

Still open for the bench: first-connect command order in practice, the wake lead / retry cadence,
and the reaction to a transmitter that drops the link right after the reply.
