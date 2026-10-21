# Board modes: 3-sensor standard BLE ↔ 1-sensor Dexcom G6 (xDrip-readable)

Status: plan, revised 2026-10-10 (v2: one image, runtime mode — replaces v1's "separate 1-sensor image")
Supersedes: `.scratch/thesis-stabilization/issues/12-dexcom-real-g6-compat.md`. Issue 09's
"honest baseline" wording stays true until this ships.
Confirmed by the user: **G6 only** (no G7).

## Goal

One firmware image, two **board modes** chosen from the app and remembered by the board:

| | **Multi — standard BLE** (today's behaviour) | **Dexcom — G6 transmitter** (new) |
|---|---|---|
| Simulated sensors | 3 (`CONFIG_APP_SENSOR_COUNT`) | **1** (slot 0) |
| Protocol | Bluetooth SIG CGMS `0x181F`, one instance per identity | Dexcom G6 GATT (`0xFEBC`, `…3532` family), AES challenge auth |
| Security | none — `CGMS_NO_AUTH`, no pairing | none either: auth is the G6 challenge; reports "already bonded", no SMP |
| Glucose cadence | every tick (model/CSV rate × speed) | **one reading per 5 minutes**, held until the next |
| Readers | the PyQt app, one tab per sensor | **xDrip on a phone** *and* the PyQt app |
| Speed multiplier | free | ×1 (xDrip polls in real time) |

In Dexcom mode xDrip (NightscoutFoundation/xDrip, Android, G6 native + *use transmitter
algorithm*) pairs with the board and shows the simulated glucose. That third-party read is the
thesis payoff.

## Non-goals

G7 / J-PAKE; real calibration, sensor failure, 10-day sessions, battery drain (fixed valid
values); more than one Dexcom transmitter; passing Dexcom's own app.

## What exists today (checked in the code, 2026-10-10)

- `struct sim_config` already has `sensor_count` and a persisted `comm_profile`; today
  `sensor_count` is initialised from Kconfig and `comm_profile` is **forced to SIG when
  sensor_count > 1**; `main.c`'s profile code is `#if N_SENSORS == 1` (compile-time).
- Three identities / adv sets / CGMS instances are created at boot (`BT_ID_MAX=3`,
  `BT_MAX_CONN=3`); every identity advertises `"Nordic Glucose Sensor N"`; the app binds a session
  to a CGMS instance from the **trailing digit of the advertised name**.
- `main_apply_comm_profile()` drops the client and re-advertises when the profile byte changes.
- The *Comm profile* characteristic (read + write, persisted) exists; its UI was removed in
  2026-10 pending a refactor. `dexcom_service.c` is a project-invented 14-byte imitation on the
  wrong characteristics (Control on `…3535`, glucose on `…3538`, no CRC) — replaced, not extended.
- `disconnected()` treats any link shorter than `SHORT_LINK_MS` as a connect storm and backs the
  re-advertise off exponentially. **A G6 poll is a short link by design** → must be exempted.
- Per-slot config (user, model, events, CSV) lives in `slots[3]`; sensor-select is a session cursor.

## Board modes — design

**One setting, one byte.** Reuse the existing *Comm profile* characteristic as the **board mode**
(`0` = Multi, `1` = Dexcom; same wire, same UUID, old harnesses keep working). The app labels it
"Board mode". Add one persisted field next to it: the **transmitter ID** (6 × `[0-9A-Z]`, default
`8SIM00`), `sim_config` version 7 (a v6 image still loads), its own characteristic registered
**last** in the GATT table (handles of everything earlier stay put), UUID after `…1a00`'s current
last one.

**Effective sensor count is derived, not stored:** `Dexcom → 1`, `Multi → CONFIG_APP_SENSOR_COUNT`.
`working_cfg.sensor_count` is set from the mode at load and on every switch; the per-slot data of
slots 1–2 stays in flash untouched while in Dexcom mode, and slot 0 (its user, model, events, CSV)
is the sensor that runs in both modes.

**Identity layout per mode** (the point: phone and PC are both connected at once, and a G6 poll
never evicts the app):

| Identity | Multi | Dexcom |
|---|---|---|
| 0 (factory address) | `Nordic Glucose Sensor 1`, SIG CGMS | **`Dexcom` + ID chars 4–5**, `0xFEBC`, G6 service → the phone |
| 1 | `Nordic Glucose Sensor 2` | **`Nordic Glucose Sensor 1`**, SIG CGMS instance 0 + config service → the PyQt app |
| 2 | `Nordic Glucose Sensor 3` | not advertised |

Identity 0 keeps the G6 role because it is the only identity where Windows pairing is
hardware-proven — if Android insists on bonding, the fallback works there. The app needs no
protocol change in Dexcom mode: the name ends in "1" → instance 0, slot 0, exactly as in Multi.
(Verify on hardware: a connection on identity 1 receives instance-0 notifications, which is how
`bt_cgms_notify_meas(conn=NULL)` should behave.)

**Switching** (write to the mode characteristic, or to the ID characteristic in Dexcom mode):
persist → stop all adv sets → disconnect every link → set the mode → reset the simulation clock
and instant events (as a Speed write already does; in Dexcom mode also force speed to ×1) →
re-arm advertising per the table. `arm_adv(i)` and the disconnect handler get an `identity_active(i)`
gate so an inactive identity is never re-advertised. The app reconnects automatically
(`BluetoothWindow.reconnect()`) and shows "board restarting…" meanwhile; the harnesses settle on
`reset_sync` afterwards (same discipline as the speed write).

**Three ways to switch, one code path.** All of them call the same `board_mode_set()` (persist →
drop links → reset → re-advertise, as above), so the result is identical whichever is used:

1. **BLE command** — a write to the Board-mode characteristic (the app's *Apply*, or a harness).
   Like every config write it is refused while the board is in *CGMS-only* mode.
2. **Button** — **hold Button 1 for 3 s** to toggle Multi ↔ Dexcom. A short press does nothing
   (switching drops every link and resets the simulation, so it must not happen by accident). While
   the button is held both mode LEDs flash fast; releasing early cancels. The button is also the
   **escape hatch**: it works when BLE is unreachable, and it is **not** blocked by CGMS-only mode.
3. *(Boot)* the saved mode, with no input.

**LED indication** (DK library numbering, in board order; the nRF54L15 DK silkscreen starts at LED0,
so these are the silkscreen LED1 and LED2):

| LED | Meaning |
|---|---|
| 1st (`DK_LED1`) | unchanged: blinks while nothing is connected, steady on when a client is connected |
| **2nd (`DK_LED2`)** | **blinking = Multi mode (3 sensors, standard BLE, no auth)** |
| **3rd (`DK_LED3`)** | **blinking = Dexcom G6 mode (1 sensor)** |

Exactly one of the 2nd/3rd LEDs blinks at any time (1 Hz, 50 % duty), the other is off, so the mode
is readable at a glance and independent of any connection. During a switch both light **steady for
about 1 s**, then the new mode's LED starts blinking (a confirmation that the board actually switched,
including for a button-triggered switch with no app attached). The blink is driven by the mode
variable itself, not by advertising state, so it stays correct if advertising is later in its
connect-window "off" phase. Which of the DK's four LEDs/buttons are used is a constant in one place
(`main.c`) so a different layout is a one-line change.

**App UI.** Configuration window → *Board* group: mode choice ("3 sensors · standard BLE, no
pairing" / "1 sensor · Dexcom G6 for xDrip"), transmitter-ID field (shown and validated only in
Dexcom mode), *Apply* with a one-line warning ("the board restarts and the simulation resets").
The slot count comes from the mode read back from the board (1 or 3), so Dexcom mode shows **one**
tab and the Users/slot UI offers one slot. `board_layout.MAX_SLOTS` stays the hardware maximum (3);
add `active_slots(mode)`.

## The PC app in Dexcom mode

The app is a first-class client in both modes, not an afterthought:

- **It connects to identity 1** ("Nordic Glucose Sensor 1"): SIG CGMS instance 0 + the config
  service. Everything the app does in Multi mode keeps working for slot 0 — Start/Pause/Stop, Send
  to…, Read from…, Insert Food/Exercise/PISA, CSV upload, board name, graphs, parity metrics.
- **It must never grab identity 0.** The G6 transmitter takes one connection at a time; if the app
  (auto-reconnect, "connect all", a click) connected to `Dexcom…` it would lock the phone out.
  The Bluetooth window lists a `Dexcom…` device as "G6 transmitter — for xDrip" and does not offer
  Connect on it; reconnect logic skips it.
- **Mode changes and windows are invisible to it** apart from the planned drop: the identity-1 link
  is not part of the 30 s connect window, and a G6 poll on identity 0 does not disturb it. A mode
  switch drops it and it reconnects to whichever identity now carries "Sensor 1" (identity 0 in Multi,
  identity 1 in Dexcom) — the existing name-based session binding already does that.
- **Radio budget**: PC (Windows adapter) holds up to 3 links in Multi mode today; in Dexcom mode it
  holds 1 (app) + optionally 1 (the Python G6 client, same adapter) while the phone holds 1 — within
  `BT_MAX_CONN=3` and the 30–50 ms interval already requested.
- **Windows quirks** carry over: the first connect to a new identity/characteristic after a firmware
  update needs `use_cached_services=False`; unpaired links only (no SMP anywhere).

## Real-time behaviour in Dexcom mode (the 5-minute rule)

A G6 produces a reading every 5 minutes and the phone fetches it; it never streams. So:

1. **Reading grid.** At sim-time multiples of 300 s the board *samples* the model/CSV value
   (sensor noise included), clamps it to 40–400 mg/dL, and publishes `{seq++, ts, glucose, trend}`.
   The model keeps ticking underneath; between boundaries nothing new is published. CSV data
   (already a 5-minute Dexcom grid, `DEFAULT_INTERVAL_S = 300`) maps one row per reading.
2. **One truth for both readers.** The held reading is what the G6 replies carry **and** what the
   SIG CGMS notification to the app carries — pushed once per reading, not every tick. The app's
   graph therefore shows exactly what xDrip shows (dots every 5 min over its continuous local
   model line); parity compares the board's reading with the model at that sim time.
3. **Speed.** xDrip schedules on wall-clock 5 minutes. Entering Dexcom mode sets ×1; the app shows
   "xDrip needs ×1" if the user raises it. At ×N the grid shrinks to 300/N wall-seconds — fine for
   the Python harness, invisible to xDrip. `ts` is sim seconds since the mode started; xDrip stamps
   readings with its own receive time, so it never sees the acceleration.
4. **Connect window (keeps xDrip locked to the grid).** xDrip wakes at
   *last receive + ≈5 min − a small lead*. If the board were always connectable, an early wake gets the
   same reading again and xDrip loses a whole cycle. So identity 0 behaves like a real transmitter:
   **connectable for `[T_k, T_k + 30 s]` around each reading, then advertising stops**
   (extended while a client is mid-handshake, never cut). The first window opens at once on
   mode entry / boot so setup is immediate, and the grid is anchored there. Window length is a
   constant (≥ the reading period when ×N makes the grid shorter, i.e. always open). Identity 1
   (the app) is unaffected and always connectable. *Needs the ticket-01 numbers: xDrip's real lead and
   retry behaviour.*
5. **State byte.** `0x06` OK from the first reading (warm-up emulation, `0x02` for the first
   sim-hours, is an optional toggle — off by default so the demo shows glucose immediately).
6. **Backfill (optional).** The board keeps the last 36 readings (3 h) in RAM; if xDrip asks for
   backfill after missing windows it gets them on `…3536`; otherwise an empty/"nothing" reply.
   First thing cut when time is short.
7. **Mode switch / reset** clears the ring, restarts `seq` and `ts`, and re-opens the window.

## What xDrip needs from a transmitter (source-read, 2026-10-10)

Paths under `app/src/main/java/com/eveningoutpost/dexdrip/`. Little-endian; every message except
the auth ones ends in **CRC-16/XMODEM** (poly 0x1021, init 0 — `g5model/FastCRC16.java`).

| UUID (base `F8083532-849E-531C-C594-30F1F86A4EA5`) | Role | Properties |
|---|---|---|
| `0xFEBC` | in the advertisement | — |
| `…3534` Control | commands in, replies out | write + **indicate** |
| `…3535` Authentication | handshake | write + read + **indicate** |
| `…3536` Backfill | backfill stream | notify |
| `…3538` ExtraData | xDrip subscribes before auth | notify |
| `0x180A` | Device Information | — |

**Auth** (`Ob1G5StateMachine`, all on `…3535`): client `01 | token[8] | 02` → board
`03 | tokenHash[8] | challenge[8]` with `hash(x) = AES-128-ECB(key, x‖x)[0:8]`,
`key = ASCII("00"+ID+"00"+ID)`; **xDrip verifies the board's `tokenHash`**, so the board must know
the ID → client `04 | hash(challenge)[8]` → board `05 | authenticated=1 | bonded`. We answer
`bonded=1` and put no encryption on any characteristic (no SMP, so no pairing problem); fallback,
if Android insists: Just-Works bonding on identity 0.

| Request (Control) | Reply |
|---|---|
| `4E` (`EGlucoseTx`) | `4F status seq[4] ts[4] glu[2] state trend pred[2] CRC` — **the one that matters** |
| `30` (`GlucoseTx`) | `31 status seq[4] ts[4] glu[2] state trend CRC` |
| `2E` (`SensorTx`) | `2F status ts[4] unfiltered[4] filtered[4] CRC` — xDrip's own-calibration mode, best effort |
| `24` (`TransmitterTimeTx`) | `25 status curTime[4] sessionStart[4] CRC` |
| version / battery / session start-stop / calibrate | fixed valid replies; opcodes confirmed in ticket 01 |
| `09` / `0A` | disconnect / unbond |

`glucose = u16 & 0x0FFF` (bits `0xF000` = display-only); `state 0x06` OK; `trend` int8 in tenths of
mg/dL/min, `127` invalid; `status 0` OK. Unknown opcodes: logged on the console, ignored.

## Decisions (recommended default)

1. **One image, runtime mode** (this version). No second build, no Kconfig switch; the existing
   3-sensor behaviour and its tests must not change in Multi mode.
2. **Phone on identity 0, app on identity 1** in Dexcom mode (table above). Fallback: one client at a time.
3. **Transmitter ID is a board setting**, written from the app; it is the AES secret and fixes the
   advertised name.
4. **Protocol logic in a pure C module** (`dexcom_g6.c/.h`, no Zephyr) host-tested like `cgmsim/`
   (`mintest.h`), with a Python twin sharing golden vectors. AES injected: PSA Crypto
   (`PSA_ALG_ECB_NO_PADDING`, nRF54L15 CRACEN) on the board, a reference AES in tests; small AES-128 C
   file if PSA costs too much flash/RAM.
5. **The app does not need a G6 reader to display Dexcom mode** (it reads SIG CGMS on identity 1).
   The Python G6 client is the *hardware test harness* and a "G6 check" that can be pointed at the
   board when no phone is around.

## Tickets (`issues/`)

| # | Ticket | Blocked by | Outcome |
|---|---|---|---|
| 01 | Spike: xDrip scan / connect / bond / command order / timing | — | settles the "confirm in 01" items |
| 02 | G6 codec, Python + pure C, golden vectors | 01 | CRC, AES hash, all messages; host tests green |
| 03 | Python G6 client harness | 02 | runs xDrip's exact sequence against any G6 peripheral |
| 04 | **Firmware: runtime board mode** (no G6 yet) | — | switch Multi ↔ "Dexcom-shaped" identity layout; persisted; app-free hardware test |
| 05 | Firmware: G6 GATT + advert + auth | 02, 04 | harness authenticates; wrong ID refused |
| 06 | Firmware: 5-minute readings, replies, connect window | 05 | harness reads grid-aligned readings matching the model |
| 07 | App: Board-mode UI, transmitter ID, slot count, E2E rewrite | 04 (ID: 05) | switch modes from the app; one tab in Dexcom mode |
| 08 | xDrip bench + docs + `.tex` | 06 (07 for the demo) | xDrip shows live glucose; thesis text updated |
| 09 | **Overnight headless test: Dexcom + mode cycles** | 06, 07 (03 for the G6 client) | `overnight_users.py` runs Multi, Dexcom-accelerated and Dexcom-real-time cycles unattended |

04 can start today: it is useful on its own (it makes the existing comm-profile switch real for a
3-identity image) and de-risks the identity/advertising rework before any crypto exists. 05 writes
ADR `0007-board-modes-multi-and-dexcom-g6`.

## Test strategy

- **Host**: CRC / hash vectors (CRC cross-checked with xDrip's `FastCRC16Test`, AES with an
  independent implementation), message round-trips, bad CRC / short / unknown opcode; reading-grid
  arithmetic (boundaries at 300 sim-s, clamp, seq) as pure functions.
- **Hardware, mode switching** (04, 07): Multi→Dexcom→Multi with the app connected; each time the
  expected identities advertise and nothing else; links drop and the app returns; slots 1–2's
  config survives a round trip; a power-cycle boots into the saved mode.
- **Hardware, G6** (05, 06): auth right/wrong ID; `seq` advances once per 5 sim-min not per request;
  poll at the window edge and just before it; hours-long run; two links (harness + app) at once.
- **Overnight, headless** (09): the existing Users overnight test gains Dexcom cycles — the real
  `MainWindow` (offscreen) as the PC app on identity 1 plus a Python G6 client playing xDrip on
  identity 0, a night mixing Multi and Dexcom cycles and ending back in Multi.
- **Manual, not automatable**: the 3 s button hold and the 2nd/3rd LED check (a checklist in
  ticket 08's pre-flight; the BLE switch path is fully automated).
- **xDrip bench** (08) is the only test that proves the claim.
- Existing 731 pytest tests and the Multi-mode E2E suites stay green.

## Risks

- **xDrip hidden preconditions** (scan filter, `AdvMatcher` payload, backfill expectations) → ticket
  01 first, plus the console log of unknown opcodes.
- **Connect-window timing vs xDrip's lead** is a guess until 01; if windows misalign xDrip, widen
  the window or fall back to "always connectable, reading republished at T_k".
- **Two identities live at once** reintroduces the multi-link radio budget (30–50 ms intervals
  already requested) — verify with the phone connected.
- **Android bonding** (if `bonded=1` is refused) → Just-Works on identity 0; beware the app's fixed
  passkey callbacks (`auth_app_passkey`): a passkey prompt on the phone is acceptable only as a last resort.
- **Windows GATT cache**: the new ID characteristic is invisible on the first connect per identity
  after a firmware update (refresh with `use_cached_services=False`).
- **Schedule**: drop-dead **2026-10-30** (issue 12). Cut order: backfill → SensorTx/GlucoseTx →
  warm-up toggle → two-link coexistence. Never cut: 04, 05, 06 and the xDrip bench.
- **Wording**: "interoperates with the G6 BLE protocol as documented by the xDrip project", not
  "a Dexcom product".

## Open questions

1. Is a 30 s connect window acceptable for the demo ("the board is only connectable at the
   reading"), or should identity 0 be always connectable at the cost of xDrip possibly lagging a cycle?
   (Recommended: window; ticket 01 will say which one xDrip tolerates.)
2. Which Android phone/version is the bench device; is xDrip installed? (Issue 12 says the device
   exists.)
