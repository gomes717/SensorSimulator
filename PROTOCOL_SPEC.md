# Simulator project spec

Authoritative reference for this project: a PyQt6 desktop app (`src/`) that
configures a simulated diabetic patient (a "person" running one of four
physiological models) and a CGM sensor noise model, sends that configuration
to an nRF54L15DK board over BLE, and compares the board's live CGM stream
(model + on-device sensor noise) against the same model run purely in
Python (no noise) in parallel. This doc is the contract between this repo
and the firmware at `C:\ncs\v3.3.1\nrf\samples\bluetooth\peripheral_cgms`,
which is a **separate, non-git-tracked project tree** — treat every byte
layout below as load-bearing, not just descriptive, since nothing here can
be re-derived by reading the firmware's own source (most of it doesn't exist
yet / isn't visible to whoever implements it).

## 1. Component map

### Python app (`src/`)

| Module | Responsibility |
|---|---|
`src/` is organized by layer (reorganized 2026-08-18 — imports are
package-qualified, e.g. `from api import protocol`, `from services.ble_session
import BleSession`, `from graphic.main_window import MainWindow`):

| Module | Responsibility |
|---|---|
| `main.py` | Entry point, creates `QApplication` + `MainWindow` |
| `api/ble_uuids.py` | Custom service/characteristic UUIDs (§2) |
| `api/protocol.py` | Binary encode/decode for every config characteristic (§2) |
| `services/ble_session.py` | `BleSession` — persistent per-device connection, own asyncio loop; decodes CGM Measurement + Food/Exercise Status notifications; `queue_write()`/`request_read()` for the config service |
| `services/bluetooth_scanner.py` | `BluetoothScanThread` — BLE discovery |
| `services/windows_ble_pairing.py` | Windows-specific passkey pairing helper (local-imported by `services/ble_session.py`) |
| `core/ble_message_log.py` | Central append-only message log shared by Debug/graph |
| `gui/main_window.py` | Toolbar, user treeview, glucose graph (received solid + expected dashed), food/exercise graph, Person/Sensor selector bar, Fast-mode + Model-Only toggles |
| `gui/bluetooth_window.py` | Device list, multi-device connect/disconnect, `sessions()`/`display_name()` accessors used by config windows |
| `gui/debug_window.py`, `gui/message_detail_window.py` | Raw message inspection |
| `gui/device_target.py` | `DeviceTargetBar` — shared "target device" combo used by all 4 config windows |
| `gui/person_config_window.py` | Manage `PersonProfile`s: model + params, Save/Send/Read |
| `gui/sensor_config_window.py` | Manage `SensorProfile`s: noise model + params, Save/Send/Read |
| `gui/food_config_window.py` | Recurring-daily meal schedule for the active person, Save/Send/Read |
| `gui/exercise_config_window.py` | Recurring-daily exercise schedule for the active person, Save/Send/Read |
| `gui/instant_event_dialog.py` | "Insert Food/Exercise Now" dialogs — one-shot events injected into an already-running simulation without resetting it (§2's instant characteristics) |
| `models/types.py` | `ModelId`, `SensorId`, `PersonProfile`, `SensorProfile`, `FoodEvent`, `ExerciseEvent` |
| `models/{cambridge,uva_padova,royparker,deichmann}.py` | Pure-Python ports of `cgmsim/src/cgmsim_*.c`, numerically identical |
| `models/sensors.py` | Default params only for the 3 sensor types (noise math is on-device only) |
| `models/engine.py` | `SimulationEngine` — runs one model in real time, 1 tick/wall-second, emits `expected_reading` |
| `models/profile_store.py` | JSON persistence of profiles to `data/profiles.json` |
| `utils/` | Reserved for generic helpers — empty for now, nothing in `src/` is generic enough to belong here yet |
| `cgmsim/` | Original standalone CLI simulator — source of truth for the model math (`FORMULAS.md`, `inc/`, `src/`) |

### Firmware (`firmware/peripheral_cgms`, board `nrf54l15dk/nrf54l15/cpuapp`)

| File | Responsibility |
|---|---|
| `src/main.c` | Boot: LEDs, BT init, `sim_config_load_from_flash()`, N BLE identities + adv sets + CGMS instances (`CONFIG_APP_SENSOR_COUNT`, §7), spawn `model_thread` + `comm_thread` |
| `src/models/cgmsim_*.{c,h}` | Verbatim copies of this repo's `cgmsim/{inc,src}/cgmsim_*.{c,h}` (4 models + sensors + types) — zero OS deps |
| `src/sim_config.{c,h}` | `struct sim_config`, flash load/save/defaults (§4) |
| `src/config_service.{c,h}` | Custom GATT service: read/write/notify handlers (§2) |
| `src/model_thread.{c,h}` | Owns model/sensor state, 1 s tick, `dt_min` per mode, food/exercise scheduling (§3) |
| `src/comm_thread.{c,h}` | Owns BLE: advertising, CGMS measurement push, Food/Exercise Status notify, drains config write queue, persists to flash |
| `boards/nrf54l15dk_nrf54l15_cpuapp.overlay` | `fixed-partitions` on the external `mx25r64` SPI-NOR (`sim_storage_partition`) |

## 2. BLE service & characteristics

Custom 128-bit UUIDs (`src/ble_uuids.py`), all under one primary service:

| Name | UUID | Properties | Payload |
|---|---|---|---|
| Config service | `5b2c0001-0d6d-4a3a-8c1e-3f9b6e7a1a00` | — | — |
| Person config | `5b2c0002-...` | **read + write** | 137 B |
| Sensor config | `5b2c0003-...` | **read + write** | 57 B |
| Mode | `5b2c0004-...` | **read + write** | 1 B |
| Food event | `5b2c0005-...` | write (appends one) | 8 B |
| Exercise event | `5b2c0006-...` | write (appends one) | 8 B |
| Food/Exercise status | `5b2c0007-...` | notify | 10 B (per slot) |
| Food events readback | `5b2c0008-...` | read (full list) | up to 257 B |
| Exercise events readback | `5b2c0009-...` | read (full list) | up to 257 B |
| Run state | `5b2c000a-...` | **read + write** | 1 B |
| Reset sync | `5b2c000b-...` | notify | 1 B |
| Food instant event | `5b2c000c-...` | write (one-shot) | 6 B |
| Exercise instant event | `5b2c000d-...` | write (one-shot) | 6 B |
| CGMS Only | `5b2c000e-...` | **read + write** | 1 B |
| CSV control | `5b2c000f-...` | write + notify | 1–18 B in, 6 B notify |
| CSV data | `5b2c0010-...` | write (chunk) | 5–~240 B |
| Data source | `5b2c0011-...` | **read + write** | 1 B |
| Speed | `5b2c0012-...` | **read + write** | 4 B (float32) |
| PISA instant event | `5b2c0013-...` | write (one-shot) | 6 B |
| Comm profile | `5b2c0014-...` | **read + write** | 1 B |
| Sensor select | `5b2c0015-...` | **read + write** | 1 B |

All multi-byte fields little-endian. Requires ATT MTU >= 140 B
(`CONFIG_BT_L2CAP_TX_MTU=247` on the firmware side). Every read/write payload
stays under the BLE spec's 512-byte max ATT attribute value — this is why
food/exercise events use a separate write-one/read-all pair of
characteristics rather than one bidirectional one (the full `sim_config`
struct, at ~2.85 KB with four sensor slots, would not fit a single attribute).

**Multi-sensor (2026-09, restored).** The board runs `CONFIG_APP_SENSOR_COUNT`
(1–4, default 4) fully independent sensor slots — each its own BLE identity +
advertising set + CGMS service instance + config slot (model/params/noise/
schedule *or* a CSV, mixable). The config service above is registered **once**,
not per slot; the **Sensor select** characteristic (`5b2c0015`) is a session
cursor that picks which slot the per-sensor reads/writes (person, sensor,
data-source, food/exercise events + readbacks, CSV upload) target. Speed, run
state, CGMS-only, comm profile, and reset sync are **global**. `sensor_count`
== 1 is the original single-sensor build and is byte-identical on the wire.
See §7.

### Person config — `struct { uint8_t model_id; float params[34]; }`

`model_id`: `0`=Cambridge, `1`=UVA/Padova, `2`=Roy&Parker, `3`=Deichmann.
Read returns the currently *applied* config in this same shape (so the app
can recover/confirm board state, e.g. after a reconnect). `params[34]` (34 =
UVA/Padova's 33 + 1 slack), field order:

- **Cambridge (17)**: `BW, VG, VI, k12, ka1, ka2, ka3, SIT, SID, SIE, ke, tmaxI, tmaxG, AG, EGP0, F01, Gpeq`
- **UVA/Padova (33)**: `BW, VG, VI, k1, k2, m1, m2, m4, kmin, kmax, kgri, kabs, ki, Fcns, Vm0, Vmx, Km0, p2u, kp1, kp2, kp3, ke1, ke2, ka1, ka2, kd, Td, bmeal, cmeal, f, HEeq, Gpeq, Ib`
- **Roy & Parker (22)**: `Gpeq, BW, VolG, Ib, u1b, p1, p2, p3, p4, n, a1, a2, a3, a4, a5, a6, k, T1, kG, Tasc, Tmax, Tdes`
- **Deichmann (22)**: `Gpeq, BW, Gb, Ib, HRb, p1, p2, p3, alpha, beta, tauHR, tau, f, AG, Vg, tau_m, k21, kd, ka, ke, Vi, IIRb`

Matches each model's C parameter struct field order exactly
(`cgmsim/inc/cgmsim_*.h`) — see `src/models/{cambridge,uva_padova,royparker,deichmann}.py`'s
`PARAM_NAMES`. Model math (ODEs, Euler step, steady-state init) is exact C
source in `cgmsim/src/cgmsim_*.c`, zero OS deps, copy essentially verbatim.

### Sensor config — `struct { uint8_t sensor_id; float params[14]; }`

`sensor_id`: `0`=Ideal CGM, `1`=Breton & Kovatchev 2008, `2`=Facchinetti et
al. 2014. Read/write symmetric, same as person config. `params[14]` (14 =
Facchinetti's 12 + slack):

- **Ideal (0 params)**: stateless — always returns the true value, nothing to configure.
- **Breton (4)**: `pacf, sigma, alpha, beta`
- **Facchinetti (12)**: `a0, a1, a2, b0, b1, b2, aw1, aw2, sigma_v, ac1, ac2, sigma_c`

Matches the configurable subset of `cgmsim/inc/cgmsim_sensors.h`'s state
structs (excludes internal-only fields the firmware seeds/tracks itself:
noise history, `rng_state`). Sensor math source: `cgmsim/src/cgmsim_sensors.c`.

**No sensor has a `sampling_time_min` field** (removed 2026-08-18). Every
sensor emits a fresh reading — noise recomputed, calibration drift advanced
— on **every** `model_thread` tick, unconditionally; there is no internal
"wait N minutes before the next sample" gate. How often the *client* actually
sees a new value is purely a transport-cadence question: it's bounded by
`comm_thread`'s own poll interval (~500 ms) and how often `model_thread`
ticks (§6) — not by anything inside the sensor model. This was a deliberate
change from the original design (which had each sensor throttle its own
`valid` flag to a configurable `sampling_time_min`, default 5 min, mirroring
real CGM hardware's actual sampling rate) — the throttling made the
board-side data update no faster than the (typically real-time-paced)
5-minute default, which was confusing paired with the independent BLE
notify cadence: the client received a notification every few seconds
regardless (the standard CGMS library re-notifies its last stored record on
its own timer — see §4.3 of `docs/FIRMWARE.md`), but the *value* inside only
changed every 5 minutes, looking frozen. If you want a slower/faster
effective update rate now, control it at the transport layer, not the
sensor.

### Mode — 1 byte, read + write (legacy, inert)

`0` = normal, `1` = fast (60x). **Superseded by Speed (below).** Removed from
`struct sim_config` in v5; the characteristic (`5b2c0004`) is kept only so the
GATT attribute layout stays stable for already-paired clients — reads return a
constant `0`, writes are accepted and dropped. The app no longer writes it.

### Speed — `5b2c0012-0d6d-4a3a-8c1e-3f9b6e7a1a00`, float32 LE, read + write

Added 2026-09 (`sim_config` v3). Continuous simulation-speed multiplier,
clamped to `[1.0, 1000.0]`. **One tick per wall-clock second, always** — the
multiplier scales how much simulated time each tick advances:

```
dt_min per tick = (1.0 / 60.0) * speed_mult
```

so `x1` = real time, `x60` = the old fast mode (1 s = 1 sim-minute), `x1000` =
1 s ≈ 16.7 sim-minutes. Applied identically on the firmware
(`model_thread.c`'s `model_tick()`) and in `src/models/engine.py`. Persisted;
default `1.0`. App side: the Configuration window's **Speed slider** (log-mapped
0–1000 → x1–x1000), `MainWindow._on_speed_changed()`, `api/protocol.py`'s
`encode_speed()`/`decode_speed()`.

### Run state — `5b2c000a-0d6d-4a3a-8c1e-3f9b6e7a1a00`, 1 byte, read + write

Deliberately **not** part of `struct sim_config` and **never persisted to
flash** — this is a live session control, not configuration. `0` = stopped
(reset to the model's initial state, then held), `1` = running, `2` = paused
(held, no reset). The board **defaults to running at boot**, using whatever
config was loaded from flash (or defaults) in `model_thread_start()` — it
must work fully standalone, streaming CGM data with no app ever connected;
this characteristic exists only so that when the app *is* connected, it can
align the board's simulation clock with its own local `SimulationEngine` for
a fair "expected vs received" comparison. The MCU is never blocked waiting
for it.

The app's Start/Pause/Resume/Stop bar (`gui/main_window.py`) drives both sides:
- **Start** (from stopped, `_start_run`): (re)creates the local engine,
  anchors `self._graph_t0` to right now, and resumes it, then writes `0`
  (stopped/reset) then `1` (running) to every connected board. Both sides'
  t=0 is "the moment Start was clicked" — no wait for board confirmation
  (see "Reset sync" below for why an earlier version did wait, and why that
  was removed).
- **Pause**: writes `2`; pauses the local engine (see
  `SimulationEngine.pause()`/`resume()` — a polled flag checked each tick,
  freezing `sim_clock_min` and model state without discarding either side).
- **Resume**: writes `1` (no reset — both sides continue from where they
  paused).
- **Stop**: writes `0` (reset+hold); stops and discards the local engine,
  clears the graphs. The button also reads "Restart" in spirit — the next
  Start begins a fresh run at `t=0` on both sides.

Writing `0` (stopped) always performs the reset, whether the board was
previously running, paused, or already stopped — implemented on the firmware
side by re-feeding the currently-active config through the same
apply/reinit path `model_thread_apply_config()` uses (cheap, and reuses
that path instead of a separate reset routine), then halting ticks until the
next `1`.

**Bug, fixed 2026-09:** the STOPPED handler re-fed *the currently-active*
config, but a config write that was still queued and not yet applied (the app
sends `data_source`, then STOPPED, then RUNNING back-to-back on Start) was
overwritten by that stale copy and silently dropped — CSV playback never
engaged. The handler now only re-feeds `active_cfg` when nothing is already
pending; a pending write's own apply performs the same full reset.

### Reset sync — `5b2c000b-0d6d-4a3a-8c1e-3f9b6e7a1a00`, 1 byte, notify

Fired directly from `model_thread.c`'s `apply_config_locked()` — i.e. the
instant a reset from *any* config write (person, sensor, mode, food/exercise
event, or a run-state `STOPPED`) actually takes effect, not when comm_thread
gets around to relaying it. Payload is a free-running 1-byte generation
counter; only its **arrival time** matters, the value itself is diagnostic
only.

One consumer on the app side: **"Did my write actually land?" feedback**
(`gui/device_target.py`'s `await_send_confirmation`, used by all four config
windows' Send to Board buttons): shows "Sending to board…" then "✓ Applied
on board" on the next reset_sync arrival, or a timeout warning if none
comes. Not perfectly attributed when several writes are in flight at once
(e.g. Food's clear-then-N-events sequence each fire their own reset_sync),
but "at least one of my writes was confirmed" is the intent, not per-write
tracking.

An earlier version also used this notification for **clock sync**: Start
would prep the local engine paused, send `STOPPED`+`RUNNING`, and only
anchor `self._graph_t0` (and resume the engine) once reset_sync arrived —
eliminating the BLE round-trip + comm_thread's ~500 ms poll + model_thread's
up-to-1s tick granularity as a visible phase shift between the "expected"
and "received" lines, at the cost of Start not visibly doing anything for
up to ~2s (or the full 3s timeout, with nothing connected or Model Only).
Removed at the user's request (2026-08-18) in favor of the simpler
"click = t=0" behavior described above — the two lines can drift by that
same latency again, worse in fast mode, but Start now feels instant.

### Food event (write) — `struct { uint16_t time_of_day_min; uint16_t duration_min; float carbs_g; }`

Recurring **daily**: fires every simulated day when `sim_clock_min % 1440`
enters `[time_of_day_min, time_of_day_min + duration_min)`.
`time_of_day_min == 0xFFFF` is a sentinel meaning "clear all food events"
(ignore the rest of the payload). The app always sends clear-then-each-event
when syncing a full schedule, never a partial diff. Board stores up to 32.

**Delivery differs by model** (`src/models/engine.py`):
- *Rate-fed* (Cambridge, UVA/Padova): while inside the window, feed
  `carbs_g_per_min = carbs_g / duration_min` continuously.
- *Impulse-fed* (Roy&Parker, Deichmann): delivered once, in full, on the
  first tick the clock reaches `time_of_day_min` each day (track last-fired
  day index per event slot so it fires exactly once/day); `duration_min` is
  unused — the model's own D1/D2 (Deichmann) or trapezoidal (Roy&Parker)
  compartments do the time-spreading.

### Exercise event (write) — `struct { uint16_t time_of_day_min; uint16_t duration_min; float intensity_pct; }`

Same daily-window semantics, `0xFFFF` clears all, up to 32 stored. Only
Roy&Parker and Deichmann react (Cambridge/UVA-Padova have no exercise term —
ignore for those two). Inside a window: `exercise_pct = intensity_pct`
(Roy&Parker) and `hr_bpm = params.HRb + intensity_pct/100 * 80` (Deichmann,
fixed +80 bpm max-effort assumption). Outside any window: `exercise_pct = 0`,
`hr_bpm = params.HRb`.

### Food/Exercise status (notify) — `struct { uint8_t slot; uint8_t _pad; float carbs_g_per_min; float exercise_pct; }`

10 bytes, **one notification per active sensor slot per tick** (`slot` = 0..
`sensor_count`-1). Board reports what it is **actually** feeding that slot's
on-device model right now (post schedule-evaluation), at the same cadence as
CGM measurement pushes (every `measurement_interval` seconds, currently 5).
Ground truth from the MCU, distinct from the app's own local schedule
evaluation (used only in Model-Only/no-device mode) — see
`gui/main_window.py`'s `_on_new_message`/`_on_expected_reading` split.

`services/ble_session.py` keeps only the notification whose `slot` matches the
connected identity's own sensor index (parsed from the advertised name's
trailing digit) and drops the siblings. The legacy 8-byte payload (no `slot`/
`_pad`) is still decoded, as `slot` 0.

### Food/Exercise events readback (read) — `struct { uint8_t count; struct food_event events[32]; }` (and the exercise equivalent)

Full current list, for recovering/confirming board state — distinct from the
write-only "append one" characteristics above because the shapes differ (one
event vs. the whole list). `count` (capped at 32) followed by that many
8-byte entries in the same layout as the write struct.

### Instant food/exercise events (write) — `struct { uint16_t duration_min; float carbs_g_or_intensity_pct; }`

Added 2026-08-18 for injecting a one-shot event into an **already-running**
simulation from the app's "Insert Food Now…"/"Insert Exercise Now…" buttons
(`gui/instant_event_dialog.py`), as opposed to the recurring-daily Food/Exercise
event characteristics above. The critical difference: **every other write
characteristic in this service — person, sensor, mode, food/exercise event,
or a run-state `STOPPED` — causes the firmware to reinitialize model/sensor
state and reset `sim_clock_min` to 0** (see `model_thread.c`'s
`apply_config_locked()`); these two deliberately do **not**. `comm_thread.c`
routes `CFG_MSG_FOOD_INSTANT`/`CFG_MSG_EXERCISE_INSTANT` straight to
`model_thread_add_instant_food()`/`model_thread_add_instant_exercise()`,
bypassing `model_thread_apply_config()` entirely — no flash write, no
`sim_clock_min` reset, no `RESET_SYNC_UUID` notification (nothing to sync
to; the run just continues). No `time_of_day_min` field — these start
"now", tracked as `remaining_min` counting down from `duration_min` in a
small fixed pool (`MAX_INSTANT_EVENTS = 8` slots in `model_thread.c`;
dropped, logged, if all slots are full), not part of `struct sim_config`
and never persisted.

**Bug, fixed 2026-08-19:** the above is about what happens when an instant
event is *added* — it says nothing about what happens to an already-active
instant slot when some *other* write triggers a reset afterward. Until this
fix, `apply_config_locked()` reset `sim_clock_min`/model state/
`last_fired_day[]` but left `instant_food[]`/`instant_exercise[]` untouched,
so an active instant event (e.g. from clicking Stop mid-event) survived the
reset and bled its remaining duration into the next run. `apply_config_locked()`
now also clears both instant-event arrays on every reset, same as its other
per-reset state.

Delivery, evaluated each tick alongside (i.e. summed/maxed with) the
recurring schedule's contribution for that tick:
- **Food, rate-fed models** (Cambridge, UVA/Padova): `carbs_g / duration_min`
  added to the tick's carb rate for every tick `remaining_min > 0`.
- **Food, impulse-fed models** (Roy&Parker, Deichmann): the full `carbs_g`
  delivered once, on the first tick after the event is added (`delivered`
  flag per slot); `duration_min` only paces when the slot is freed.
- **Exercise** (both instant and recurring only affect Roy&Parker/Deichmann):
  while `remaining_min > 0`, contributes
  `max(instant_intensity_pct, recurring_schedule's_current_value)` — an
  active instant bout can only raise exercise_pct/hr_bpm above whatever the
  recurring schedule already gives, never lower it.

`src/models/engine.py`'s `SimulationEngine.add_instant_food()`/
`add_instant_exercise()` mirror this exact logic locally (own
`_instant_food`/`_instant_exercise` lists, `threading.Lock`-guarded since
called from the GUI thread), so Model-Only mode and the "expected" line
during a BLE-connected run both reflect an inserted event the same way the
board does — instantly, without restarting the comparison run. Verified on
hardware (2026-08-18): `sim_clock_min` and Deichmann's internal state
(`Ic`/`x1`/`x2`) continued unbroken across both writes; `carbs`/`ex` in the
tick log picked up the injected values on the very next tick.

### Instant PISA event (write) — `5b2c0013-...`, `struct { uint16_t duration_min; float depth_frac; }`

Added 2026-09. Pressure-Induced Sensor Attenuation: a transient downward
attenuation of the **sensor reading** producing a *false low* that does not
reflect real hypoglycaemia. Same non-disruptive class as the food/exercise
instant events — never persisted, never resets `sim_clock_min`, routed straight
to `model_thread`'s live PISA slots (`CFG_MSG_PISA_INSTANT`).

`depth_frac` ∈ `[0, 1]` is the peak attenuation. While a bout is active,
`elapsed = duration_min - remaining_min`, `frac = elapsed / duration_min`, and
the final reading is multiplied by:

```
1 - depth_frac * sin(pi * frac)
```

— a smooth dip: no effect at the start/end, `depth_frac` attenuation at the
midpoint. Multiple bouts compound (product). Applied **after** the noise model
and **on the CSV data source too** (it is a sensor artefact, not a glucose
change — the underlying `glucose` in the tick log is untouched, only `reading`
dips). `src/models/engine.py`'s `SimulationEngine.add_instant_pisa()` mirrors it
so the "expected" line dips the same way. The app shades the affected wall-clock
interval on the glucose graph (`MainWindow._pisa_spans` / `_draw_pisa_spans()`).
Verified on hardware (2026-09): 40 % / 10 min bout drove the streamed value from
100 to ~60 at the midpoint and back to 100, `pisa` factor in the tick log
tracing `0.88 → 0.60 → 0.88 → 1.0`.

### Comm profile — `5b2c0014-0d6d-4a3a-8c1e-3f9b6e7a1a00`, 1 byte, read + write

Added 2026-09 (`sim_config` v4). Selects which BLE profile the board streams
glucose over. `0` = **SIG CGMS** (the standard `0x181F` / `0x2AA7` service,
default). `1` = a **basic Dexcom-style** imitation — *not* a real Dexcom: no
J-PAKE/AES authentication handshake, realtime glucose message only (no backfill,
no calibration). Persisted; a write re-advertises the board. Because a
connectable adv set can't restart with the single connection slot occupied
(`CONFIG_BT_MAX_CONN=1`), if a client is connected the firmware **drops the
link** and re-advertises under the new profile on disconnect; the app then
reconnects automatically (`BluetoothWindow.reconnect()`).

App side: `api/protocol.py`'s `encode_comm_profile`/`decode_comm_profile`, the
"Communication type" combo in the Configuration window (which triggers the
reconnect), and `services/ble_session.py`'s Dexcom decoder branch.

#### Dexcom-style stream

Advertising in Dexcom mode: the `0xFEBC` service UUID and the name `DXCM01`.
GATT (`firmware/peripheral_cgms/src/dexcom_service.c`), base
`F8083532-849E-531C-C594-30F1F86A4EA5`:

| Char | UUID suffix | Props | Purpose |
|---|---|---|---|
| Control | `…3535` | write + notify | writes are ACKed and ignored (no auth here) |
| Glucose | `…3538` | notify | the 14-byte realtime message below |

Realtime glucose message (LE, 14 bytes):

| Off | Size | Field |
|---|---|---|
| 0 | u8 | opcode `0x4E` (EGV) |
| 1 | u8 | status `0` |
| 2 | u32 | sequence (increments per message) |
| 6 | u32 | timestamp, seconds since boot |
| 10 | u16 | glucose mg/dL in the low 12 bits (top bits = display flags, `0` here) |
| 12 | u8 | state `0x06` |
| 13 | i8 | trend (`0` = flat) |

Speed multiplier, CSV data source, and PISA all still apply — they shape
`latest.glucose_mg_dl` upstream in `model_thread`, before `comm_thread` picks the
profile. See [`docs/BLE_PAYLOAD_VALIDATION.md`](docs/BLE_PAYLOAD_VALIDATION.md)
§2 for how this compares to a real Dexcom transmitter. Comm profile is
single-sensor only — a multi-sensor build always streams SIG CGMS and ignores
a Dexcom write.

### Sensor select — `5b2c0015-0d6d-4a3a-8c1e-3f9b6e7a1a00`, 1 byte, read + write

Added 2026-09 with the multi-sensor restore. **Not** part of `struct
sim_config`, **never persisted** — a session cursor, same category as Run
state. The board runs `sensor_count` independent sensor slots (see §5); this
byte is the slot index `[0, sensor_count)` that every subsequent **per-sensor**
config write and read targets: Person config, Sensor config, Data source, Food
event / Exercise event (+ their readbacks), the instant Food/Exercise/PISA
events, and the whole CSV upload transport (BEGIN/DATA/COMMIT/CLEAR). A write
out of range is clamped to `0`; the value survives until the next write or a
disconnect (it resets to `0` at boot). **Global** characteristics — Speed, Run
state, CGMS Only, Comm profile, Reset sync — ignore it.

Reads on the per-sensor characteristics return the *currently selected* slot's
stored values, so the app's "Read from Board" round-trips one slot at a time:
write `sensor_select`, then read/write that slot, repeat. On a `sensor_count ==
1` build the only valid value is `0` and the characteristic is a no-op.

App side (Phase 1): `api/protocol.py`'s `encode_sensor_select`/
`decode_sensor_select`, `SENSOR_SELECT_UUID` in `api/ble_uuids.py`, and the
`"sensor_select"` key in `services/ble_session.py`'s `CONFIG_CHAR_KEY_BY_UUID`.
The slot-assignment UI (assign each of the 4 slots to a person/CSV, push a
whole board layout) is Phase 2 — see `docs/TODO.md`.

### CGMS Only mode — `5b2c000e-0d6d-4a3a-8c1e-3f9b6e7a1a00`, 1 byte, read + write

Added 2026-08-18. A live session mode, not part of `struct sim_config` and
never persisted — same category as Run state above. While enabled, the
board is a **pure standard-CGM data source**: it streams nothing but CGM
Measurement notifications (the Bluetooth SIG CGMS characteristic, §1's
"standard CGM Service") and refuses every write to
person/sensor/mode/food-event/exercise-event/food-instant/exercise-instant
— `config_service.c`'s handlers for those check
`model_thread_get_cgms_only()` first and return `BT_ATT_ERR_WRITE_NOT_PERMITTED`
if set, *before* enqueueing anything. Food/Exercise Status notifications
are suppressed too (`comm_thread.c`'s `push_measurement_and_status()`
skips `config_service_notify_food_exercise_status()` while enabled). Only
this characteristic and Run state itself stay writable — that's the only
way out.

Semantics of the two transitions (`model_thread_set_cgms_only()`):

- **Enable (write `1`)**: does **not** reset anything. It just calls
  `model_thread_set_run_state(SIM_RUN_RUNNING)` — starting the simulation
  if it wasn't already running — then applies the streaming/write
  restrictions. Whatever was already in progress keeps running,
  uninterrupted, exactly like the instant food/exercise events above never
  going through `apply_config_locked()`.
- **Disable (write `0`)**: calls `model_thread_set_run_state(SIM_RUN_STOPPED)`
  — the same full reset (`sim_clock_min` back to 0, model/sensor state
  reinitialized) a Run-state `STOPPED` write always does — and then holds,
  waiting for an explicit `RUNNING` write, same as any other STOPPED
  transition. This is a deliberate exception to the board's normal
  "autonomous, defaults to running" behavior: leaving CGMS Only always
  requires an explicit Start from the app, it never auto-resumes.
- **Re-enabling** after a disable picks the restrictions back up on top of
  whatever state the simulation is in at that moment (freshly reset-and-
  stopped if nothing else happened in between, or wherever a subsequent
  Start left it) — "enable" itself never resets, per above.

App side (`gui/main_window.py`'s `_on_cgms_only_toggled`): the CGMS
Only checkbox is the trigger, not the Start button — checking it locks
every control that would send a now-rejected write (Person/Sensor/Food/
Exercise Configure, Fast mode, Model Only, Start/Pause/Stop, Insert Food/
Exercise Now) and discards the local `SimulationEngine` entirely — no
"expected" line, no local model run at all, purely a passive viewer of
whatever the board streams. `MainWindow._on_new_message`'s plotting gate
(normally keyed on `self._run_state == "running"`, driven by Start/Stop)
is bypassed in favor of `self._cgms_only` directly, since this mode has no
Start/Stop concept of its own — the board manages its run state
autonomously. Unchecking sends the disabling write and unlocks the app
again. Verified on hardware (2026-08-18): a blocked write during CGMS Only
came back as ATT error `Write Not Permitted`; Food/Exercise Status notify
count was 24 in a 12 s baseline window and exactly 0 in the following 12 s
with CGMS Only enabled; the same write succeeded immediately after
disabling.

### CSV playback data source — `5b2c0011` (data source) + `5b2c000f`/`5b2c0010` (upload)

Added 2026-09. Lets the board **replay a recorded CGM trace** instead of
running a physiological model: the app uploads a 24 h glucose window (and,
optionally, the matching Food Log) over BLE, the firmware stores it in a
dedicated external-flash partition (`sim_csv_partition`, see
[`docs/FIRMWARE.md`](docs/FIRMWARE.md)), and `model_thread` emits one row per
tick as the standard CGM Measurement — **verbatim, with no on-device sensor
noise** (the recording already carries real noise). Byte-for-byte identical
framing to model mode; only the source of the number changes. Meals (recurring
food events, Food Log CSV, "Insert Food Now") are still evaluated and reported
on Food/Exercise Status, but **do not alter the streamed glucose** — there is
no model running to perturb.

**Data source — `5b2c0011-...`, 1 byte, read + write, persisted in `sim_config`
(now v2).** `0` = physiological model (default), `1` = CSV playback. A write
resets the simulation clock/state like any other config write. If set to `1`
with no glucose track committed, the firmware falls back to the model.

**CSV control — `5b2c000f-...`, write + notify.** Opcode-tagged writes; the
notification is `{ uint8 status; uint8 _reserved; uint32 received_bytes }`
(6 B, LE), `status` 0 = OK, 1 = ERR.

| Opcode | Write payload | Effect |
|---|---|---|
| `0x01` BEGIN | `u8 op; u8 track; u16 row_count; u32 base_epoch_s; u16 interval_s; u32 total_bytes; u32 crc32` (18 B, packed LE) | Erases `ceil(total_bytes / 4096)` sectors of the track's region, arms the write cursor, notifies. |
| `0x02` COMMIT | `u8 op; u8 track` | Verifies `received_bytes == total_bytes` and CRC-32/ISO-HDLC, writes the manifest, activates the track, notifies. |
| `0x03` ABORT | `u8 op` | Discards the in-progress upload. |
| `0x04` CLEAR | `u8 op; u8 track` | Wipes that track's manifest entry. |
| `0x05` STATUS | `u8 op; u8 track` | Notifies current `received_bytes`. |

`track`: `0` = glucose, `1` = food log.

**CSV data — `5b2c0010-...`, write.** `u32 offset` (into the track) followed by
up to ~236 payload bytes. The app streams chunks with monotonically increasing
`offset` between a BEGIN and its COMMIT.

**Track byte formats** (identical on the wire and in flash, LE):

- *glucose*: `int16 mg_dl` × `row_count` (24 h @ 300 s = 288 rows = 576 B).
  Playback: `row = floor(sim_clock_min·60 / interval_s) % row_count`.
- *food log*: `{ u32 offset_s; float carbs_g }` × `row_count`, sorted by
  `offset_s`. Each meal is surfaced report-only via Food/Exercise Status,
  spread over 30 simulated minutes, looping with the glucose track.

`base_epoch_s` is diagnostic only — playback is driven by `sim_clock_min`, not
wall-clock. The board is autonomous: after reboot it reloads the manifest and
resumes CSV playback if `data_source` was `1`.

App side: `api/protocol.py`'s `encode_csv_*` / `build_*_track` helpers,
`services/ble_session.py`'s `start_csv_upload()`, the CSV Analysis window's
"Assign window to person…" button, and Configuration → "Send CSV to Board".
The matching Food Log is paired automatically by file ID
(`Dexcom_001.csv` ↔ `Food_Log_001.csv`, `food_log_csv.matching_food_log_path()`)
— no separate file chooser. `models/engine.py` replays the same window locally
so the "expected" line matches. See [`docs/BLE_PAYLOAD_VALIDATION.md`](docs/BLE_PAYLOAD_VALIDATION.md)
for how to check received == expected.

## 3. Readback / "get config from MCU" (Python side)

`BleSession.request_read(char_key)` (thread-safe, any thread) schedules a
GATT read on the session's own asyncio loop via
`asyncio.run_coroutine_threadsafe` and emits the result on
`config_read = pyqtSignal(address, char_key, raw_bytes)`, or `write_failed`
on error. `char_key` is one of `"person"`, `"sensor"`, `"mode"`,
`"food_list"`, `"exercise_list"`, `"data_source"`, `"speed"`,
`"comm_profile"`, `"sensor_select"` (see `CONFIG_CHAR_KEY_BY_UUID` in
`services/ble_session.py`); the per-sensor ones return whichever slot
`"sensor_select"` currently points at. Each config window has a "Read from Board" button next to
"Send to Board" that calls this and, on `config_read`, decodes via the
matching `protocol.decode_*` function and overwrites the selected
profile/active person's data with the board's answer (round-trip verified in
this repo — see `api/protocol.py`'s `decode_*` functions, exact inverses of the
`encode_*` ones).

## 4. Insulin / basal

No pump or bolus dosing is modeled. Each model runs at a **constant basal**
computed once when config is (re)applied:
- Cambridge: `cambridge_basal_iir_u_per_h(params)`
- UVA/Padova: `uva_padova_basal_iir_u_per_h(params)` (note: `step()` takes
  U/**min**, so divide this by 60 before passing in)
- Roy&Parker: `params.u1b` directly
- Deichmann: `params.IIRb` directly

## 5. Flash-persisted config struct (external SPI-NOR)

Board has an 8 MB external SPI-NOR (`mx25r6435f@0`, label `mx25r64`, on
`spi00`), already `status = "okay"` in the board's default devicetree but
with no partitions defined — add a `fixed-partitions` overlay
(`boards/nrf54l15dk_nrf54l15_cpuapp.overlay`) with a `sim_storage_partition`
label and use the `flash_area` API against it directly (**not** ZMS/settings,
which already targets internal MRAM for BT bonding on this SoC — keep them
separate, this is specifically "store it in external flash"). Note: the
board's own default devicetree already has a partition labeled
`storage_partition` on the internal MRAM (used by `CONFIG_SETTINGS`/ZMS) —
devicetree labels must be globally unique, which is why this one is
`sim_storage_partition` and not the more obvious name.

```c
#define SIM_CONFIG_MAGIC 0x53494D31u  /* "SIM1" */
#define SIM_CONFIG_VERSION 5   /* v2 data_source; v3 speed_mult; v4 comm_profile; v5 per-slot */
#define MAX_MODEL_PARAMS 34
#define MAX_SENSOR_PARAMS 14
#define MAX_EVENTS 32
#define MAX_SIM_SENSORS 4

struct food_event    { uint16_t time_min; uint16_t duration_min; float carbs_g; };
struct exercise_event{ uint16_t time_min; uint16_t duration_min; float intensity_pct; };

/* One sensor's fully independent pipeline (~709 B). */
struct sensor_slot {
    uint8_t  data_source;        /* 0 model, 1 CSV playback (see §2) */
    uint8_t  model_id;
    float    model_params[MAX_MODEL_PARAMS];
    uint8_t  sensor_id;
    float    sensor_params[MAX_SENSOR_PARAMS];
    uint8_t  food_count;
    struct food_event food[MAX_EVENTS];
    uint8_t  exercise_count;
    struct exercise_event exercise[MAX_EVENTS];
};

struct sim_config {
    uint32_t magic;             /* SIM_CONFIG_MAGIC; anything else => use defaults */
    uint16_t version;           /* bump on incompatible layout change */
    uint8_t  sensor_count;      /* 1..MAX_SIM_SENSORS; boot-init from CONFIG_APP_SENSOR_COUNT */
    uint8_t  comm_profile;      /* global; 0 SIG CGMS, 1 Dexcom-style; forced SIG when count > 1 */
    float    speed_mult;        /* global: x1..x1000 simulation-speed multiplier */
    struct sensor_slot slots[MAX_SIM_SENSORS];
};  /* ~2.85 KB total — fits one 4 KB erase sector, but far too big for a
     * single BLE attribute (§2's split + Sensor-select characteristics exist
     * because of this 512 B ATT limit, not the flash sector size). */
```

The legacy top-level `mode` byte (v1–v4) is **gone** from the struct as of v5;
its characteristic (`5b2c0004`) survives only as a read-constant / write-drop
no-op so already-paired clients keep a stable GATT layout.

Write the whole struct (erase + program) on every characteristic write that
changes config. Load on boot; fall back to `sim_config_set_defaults()` (every
slot Cambridge + Ideal CGM + no events + model data source, `sensor_count` =
`CONFIG_APP_SENSOR_COUNT`) if `magic`/`version` don't match — a v1–v4 flash
image reads as version-mismatch and falls back to defaults, which is the
intended upgrade path. `sensor_count` is clamped to `[1, MAX_SIM_SENSORS]` on
load.

The uploaded CSV trace + food log live in a **separate** external-flash
partition, `sim_csv_partition` (252 KB), not this struct — see §2's "CSV
playback data source" and `firmware/peripheral_cgms/src/csv_store.c`.

## 6. Threading (Zephyr, two `k_thread`s — the assignment's explicit requirement)

- **model_thread**: 1-second tick (`k_sleep(K_SECONDS(1))`).
  `dt_min = (1.0/60.0) * speed_mult` (see §2's Speed). Maintains
  `sim_clock_min` (free-running). Evaluates
  food/exercise windows (§2), dispatches to the selected model's
  `step()`/`glucose_*()`, then the selected sensor's noise `*_update()`, and
  publishes `{glucose, valid, carbs_g_per_min, exercise_pct}` under a
  `k_mutex`. Exposes `model_thread_apply_config()` to reinit model/sensor
  state (called by comm_thread after a config write).
- **comm_thread**: owns BLE — the N CGMS instances (§7),
  `bt_cgms_measurement_add()` per slot and one Food/Exercise Status notify per
  slot, both every `measurement_interval` seconds (currently 5s), reading the
  mutex-protected per-slot state from model_thread. Also drains a `k_msgq` fed
  by the config service's GATT write callbacks (which run in BT host context
  and must stay short) — on each message: update in-RAM `sim_config` (per-slot
  writes target `slots[sensor_select]`), `sim_config_save_to_flash()`,
  `model_thread_apply_config()`. Serves the read-characteristic callbacks
  directly from the in-RAM `sim_config`. Advertising itself is owned by
  `main.c` (per-identity adv sets).

Existing LED-blink `k_work_delayable` stays as-is (not part of the
two-thread requirement, which is specifically model vs. comm).

**Diagnosed bug, fixed**: `push_measurement_and_status()` originally also
gated the `bt_cgms_measurement_add()` call on a locally-tracked
`session_active` flag, set from the CGMS library's `session_state_changed`
callback and cleared on every BLE disconnect (`main.c`'s `disconnected()`).
That callback fires exactly once — at `bt_cgms_init()`, before any client
has ever connected — and is never re-armed per reconnect. So after the
*first* disconnect/reconnect cycle, every subsequent fresh reading was
silently discarded (the flag stayed false forever), while the CGMS
library's own independent periodic `report_meas()` work item kept
re-notifying whatever record last got through — producing a live-looking
but frozen glucose value on the client indefinitely. Fixed by dropping the
`session_active` gate entirely (`bt_cgms_measurement_add()` already does its
own, correct, per-call session-stopped check internally) — diagnosed via the
`comm_thread: fresh reading glucose=... session_active=...` debug log added
to `push_measurement_and_status()`.

## 7. Multi-sensor: N independent slots on one board

**History.** The upstream sample had a 4-virtual-sensor design; it was first
trimmed to a single sensor / single BLE identity (`NUM_SENSORS` /
`CONFIG_BT_CGMS_INSTANCE_COUNT` / `CONFIG_BT_ID_MAX` / `CONFIG_BT_MAX_CONN` /
`CONFIG_BT_EXT_ADV_MAX_ADV_SET` all = 1). Restored 2026-09 as **N fully
independent slots**, `N = CONFIG_APP_SENSOR_COUNT` (1–4, default 4); `N == 1`
is behaviourally identical to the single-sensor build.

**What "independent" means.** Each slot has its own `struct sensor_slot` (§5):
its own data source — a physiological model + params + sensor-noise model +
recurring food/exercise schedule, **or** its own uploaded CSV — mixable (e.g.
3 CSV + 1 model). The simulation **clock and speed multiplier are shared**: all
slots tick together from one `sim_clock_min`, one `speed_mult`, one run state.

**BLE.** Identity 0 is the factory default; identities 1..N-1 are created with
stable static random addresses (`CONFIG_BT_PRIVACY=n`, so no RPAs). Each
identity gets its own extended-advertising set (`adv_param.id = i`), its own
`bt_cgms` service instance, and a scan-response name `"Nordic Glucose Sensor
{i+1}"` (the single-sensor build keeps the bare `"Nordic Glucose Sensor"`).
`connected`/`disconnected` route by `bt_conn_get_info().id`; each identity
re-advertises independently on its own disconnect. `comm_thread`'s
`push_measurement_and_status()` loops the N slots — `model_thread_take_measurement(i)`
→ `bt_cgms_measurement_add(g_cgms[i], …)` — and emits one per-slot Food/Exercise
Status notification each (§2). The Dexcom comm profile is single-sensor only.

**App.** Each `BleSession` binds to one identity: it parses the advertised
name's trailing digit (1-based) to a 0-based `_own_instance_index` and shows
only that CGMS instance's measurements and that slot's Food/Exercise Status,
ignoring the siblings visible on the shared GATT DB. Connecting to all four
addresses gives four tree rows; the graph plots whichever tree row is selected.
A numbered identity also sets `_require_pairing = False` — `BleSession` skips
its Windows `pair_with_pin()` step for these (Option A firmware needs no
pairing, and *attempting* it wedges the Windows BLE stack into a
connect/disconnect storm).

**App — board layout (Phase 2, done 2026-09).** The **Board Layout** window
(`gui/board_layout_window.py`, opened from Configuration → "Board layout")
assigns a saved Person + Sensor profile to each of the 4 slots, persisted to
`data/board_layout.json` (`models/board_layout.py`). "Send layout to Board"
calls `BleSession.send_board_layout(slots)`: over one connection to any
identity, for each slot it writes the **Sensor select** cursor, then that
slot's `person` / `sensor` / `data_source` / cleared-then-listed food+exercise
events (paced ~50 ms apart so the firmware's 16-deep config queue keeps up),
then — if the assigned person is CSV-backed — uploads that slot's glucose +
food-log tracks (the existing `_upload_csv_sets` BEGIN/DATA/COMMIT flow, now
slot-aware), and finally sets run state RUNNING. Verified on hardware: a
Cambridge / UVA-Padova / Roy&Parker / Deichmann layout with mixed Ideal/Breton
noise read back per-slot exactly and the board's `model_tick[0..3]` lines
switched to `model=0/1/2/3` with the assigned noise.

### Pairing: Option B (tried, failed on hardware) → Option A (active)

Windows aborts LE Secure Connections against a peripheral's **non-default**
identities. **Option B** (tried 2026-09): privacy off so identities 1..N-1 use
stable static addresses, a bond slot each (`CONFIG_BT_MAX_PAIRED=8`), SC left
as Zephyr's default (no forced legacy pairing — that previously broke encrypted
GATT reads with "Insufficient Authentication"), plus peripheral-initiated
security (`bt_conn_set_security(conn, BT_SECURITY_L2)` in `main.c`'s
`connected()`). **Result on hardware:** identity 0 pairs and does encrypted
GATT fine; identities 1..N-1 fail every time — the board logs `Security failed
... level 1 err 2` (`BT_SECURITY_ERR_AUTH_REQUIREMENT`) and Windows drops the
link (`reason 0x13`). Same unresolved Windows + Zephyr multi-identity LE-SC
bug.

**Option A** (active for `N > 1`): `CONFIG_APP_CGMS_NO_AUTH` — the CGMS
characteristics use `BT_GATT_PERM_READ/WRITE` instead of `*_AUTHEN` (patch in
`firmware/overlay/nrf/subsys/bluetooth/services/cgms/cgms.c`), so all N
identities stream with no pairing at all (acceptable for a simulator). Its
Kconfig is `default y if APP_SENSOR_COUNT > 1`, so a single-sensor build keeps
the real security model and the CGMS attribute table stays byte-identical to
upstream. `CONFIG_BT_PRIVACY=n` is kept regardless so the secondary identities
have stable, scannable addresses. Verified on hardware: all four identities
("Nordic Glucose Sensor 1–4") connect with no pairing and stream their own
slot's glucose (97 / 99 / 101 / 103 mg/dL at defaults, matching each slot's
`model_tick` serial line).

## 8. Build

`firmware/peripheral_cgms`, board
`nrf54l15dk/nrf54l15/cpuapp`, build directory `build_1`:

```
west build -b nrf54l15dk/nrf54l15/cpuapp -d build_1 --pristine
west flash -d build_1
```
