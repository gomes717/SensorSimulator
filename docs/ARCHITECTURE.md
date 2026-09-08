# System Architecture

This is the top-level map of SensorSimulator: what the two halves of the
system are, how they talk to each other, and where to look for detail.
For byte-level wire format, see [`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md).
For firmware internals, see [`FIRMWARE.md`](FIRMWARE.md). For the app's
internals, see [`APPLICATION.md`](APPLICATION.md). For the physiological
models and sensor noise math, see [`MODELS.md`](MODELS.md). For a
thesis-ready LaTeX write-up of the flows below (§4) plus the Dexcom
profile, the PISA fault model, and the multi-sensor architecture with the
Windows pairing limitation, see [`architecture_flows.tex`](architecture_flows.tex).

## 1. What this project is

A TCC (Brazilian undergraduate thesis) project simulating a Continuous
Glucose Monitoring (CGM) sensor: an nRF54L15 development kit runs firmware
that behaves like a real CGM sensor over Bluetooth Low Energy — advertising,
pairing, and streaming glucose readings exactly as a real device would — but
the "patient" behind those readings is a physiological model (one of four),
optionally fed simulated meals and exercise, run entirely on the MCU. A
desktop app connects to the board, configures that simulated patient,
displays the incoming readings, and — as a correctness check — runs the
*same* model in parallel on the PC with no sensor noise, so the two curves
("received" vs. "expected") can be compared live.

## 2. The two halves

```
┌─────────────────────────────────────┐         ┌──────────────────────────────────────────┐
│         PC — SensorSimulator app      │   BLE   │        nRF54L15 DK — peripheral_cgms       │
│              (Python / PyQt6)         │◄═══════►│           (C / Zephyr RTOS)                 │
│                                        │  N BLE  │                                              │
│  graphic/  — windows & dialogs (UI)   │identity │  main.c        — N BLE identities + adv     │
│  services/ — BLE session/scan/pairing │  links  │                  sets + CGMS instances       │
│  api/      — BLE wire-format contract │         │  comm_thread   — BLE push (per slot),       │
│  core/     — shared message log       │         │                  config queue, flash        │
│  models/   — same physiological math, │         │  model_thread  — N independent slots,       │
│              run locally for "expected"│         │                  1 shared clock, 1 s tick    │
│  board_layout — slot → person/CSV map │         │  external SPI-NOR — sim_config (v5) + CSV   │
└─────────────────────────────────────┘         └──────────────────────────────────────────┘
```

The firmware runs **`CONFIG_APP_SENSOR_COUNT` (1–4, default 4) fully
independent sensor slots** on the one board — each its own BLE identity,
advertising set, CGMS service instance, and config (physiological model +
params + noise + schedule, *or* a CSV, mixable). `N == 1` is the original
single-sensor build and behaves exactly as the diagrams below describe with
"the board" = slot 0. For `N > 1` see [§9](#9-multi-sensor-n-independent-slots).

Both sides run **the same model math** (see [`MODELS.md`](MODELS.md)) —
the Python `models/` package and the firmware's `src/models/cgmsim_*.c`
files are, by design, numerically identical ports of the same source
(`cgmsim/src/cgmsim_*.c`, the project's original standalone CLI simulator).
This is what makes the "expected vs. received" comparison meaningful: any
divergence between the two lines is either sensor noise (intentional) or a
bug (not).

## 3. Why a real board instead of just simulating in Python

The assignment requires an embedded, RTOS-based (Zephyr) implementation
using real BLE hardware — the board isn't a convenience, it's the point.
Concretely, the firmware must independently prove:
- a **two-thread RTOS design** (model vs. communication — see
  [`FIRMWARE.md`](FIRMWARE.md) §1),
- **non-volatile storage** on external flash, surviving reboots,
- a real **Bluetooth GATT server** implementing the standard CGM Service
  plus a custom configuration service, working against an unmodified BLE
  client stack (Windows' own, via `bleak`).

The app is a client of that firmware, not a replacement for it — with the
board unplugged the app can still run "Model Only" mode (§ below), but that
is a debugging/demo convenience, not the deliverable.

## 4. Data exchange, end to end

Everything that crosses the BLE link falls into one of three categories:

| Category | Direction | Characteristic(s) | Detail |
|---|---|---|---|
| **Standard CGM readings** | board → app | Bluetooth SIG CGMS: CGM Measurement (notify), Feature/Status/Session (read), RACP/SOCP (control) — **one instance per sensor slot** | [`FIRMWARE.md`](FIRMWARE.md) §4 |
| **Simulator configuration** | app ↔ board | Custom "sim config" service (**one instance, shared**): person/sensor/data-source/food/exercise/run-state/speed/comm-profile/instant-events | [`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md) §2 |
| **Slot targeting** | app → board | **Sensor select** (`5b2c0015`) — a session cursor: subsequent per-sensor reads/writes hit `slots[selected]` | [`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md) §2 |
| **Per-slot ground truth** | board → app | Food/Exercise Status (notify) — 10 B, leading `u8 slot`; one per active slot per tick | [`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md) §2 |
| **Confirmation/sync** | board → app | Reset Sync (notify) — global (any slot's config apply) | [`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md) §2 |

### 4.1 Development flow (Start → streaming)

This is the normal "just run it" flow — board and app are already
configured (defaults or a previous send), and the user just wants to watch
the two curves. It's the one to reach for while developing/demoing the
model math end to end:

```mermaid
sequenceDiagram
    participant U as User
    participant App as App (graphic/)
    participant BLE as services/ble_session.py
    participant FW as Firmware (comm_thread)
    participant Model as Firmware (model_thread)

    U->>App: Start
    App->>App: anchor local SimulationEngine at t=0
    App->>BLE: queue_write("run_state", RUNNING)
    loop every 1 s (MCU tick)
        Model->>Model: step physiological model + sensor noise
    end
    loop every 5 s (measurement_interval)
        FW->>BLE: CGM Measurement notify (glucose)
        FW->>BLE: Food/Exercise Status notify (carbs rate, exercise %)
        BLE-->>App: new_message signal
        App->>App: append to "received" graph
    end
    App->>App: local engine ticks every 1 s, appends to "expected" graph
```

### 4.2 Send configuration flow

Every config window's "Send to Board" button (Person, Sensor, Food,
Exercise) drives this — the write always triggers a full reset on the
board side (`apply_config_locked()`), which is how the app knows the write
landed. On a multi-sensor board the write targets whichever slot the
**Sensor select** cursor points at (default slot 0):

```mermaid
sequenceDiagram
    participant U as User
    participant App as App (graphic/*_config_window.py)
    participant BLE as services/ble_session.py
    participant FW as Firmware (comm_thread)
    participant Model as Firmware (model_thread)

    U->>App: Edit Person/Sensor/Food/Exercise, click "Send to Board"
    opt targeting a specific slot (N > 1)
        App->>BLE: queue_write("sensor_select", slot)
        BLE->>FW: GATT write — comm_thread sets working_sel
    end
    App->>BLE: queue_write(char_key, bytes)
    BLE->>FW: GATT write (config characteristic)
    FW->>FW: update in-RAM sim_config.slots[working_sel], save to external flash
    FW->>Model: apply_config() — reinit every slot, sim_clock_min = 0
    Model-->>FW: reset_sync notify
    FW-->>BLE: notification
    BLE-->>App: reset_sync signal
    App-->>U: "✓ Applied on board" (device_target.py await_send_confirmation)
```

> **Note.** A **Speed** write (`5b2c0012`) currently goes through this same
> `apply_config_locked()` path (it resets the clock and clears instant
> events). See `FEATURE_IDEAS.md` #21 — making speed a live scalar is a
> pending fix; `e2e_4sensor.py` works around it by waiting for `reset_sync`
> before firing an instant event after a speed change.

### 4.3 Read configuration flow

Every config window's "Read from Board" button drives this — a plain
GATT read/response, no notify, no reset (see [`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md)
§3):

```mermaid
sequenceDiagram
    participant U as User
    participant App as App (graphic/*_config_window.py)
    participant BLE as services/ble_session.py
    participant FW as Firmware (comm_thread)

    U->>App: Click "Read from Board"
    App->>BLE: request_read(char_key)
    BLE->>FW: GATT read (config characteristic)
    FW-->>BLE: raw bytes (in-RAM sim_config)
    BLE-->>App: config_read(address, char_key, raw_bytes)
    App->>App: protocol.decode_*(raw_bytes)
    App->>App: overwrite selected profile / active person data
```

### 4.4 Instant food/exercise events

One more write deliberately skips the reset flow in §4.2: "Insert Food
Now…"/"Insert Exercise Now…" (`graphic/instant_event_dialog.py`) injects a
one-shot event into an already-running simulation without resetting
`sim_clock_min` or model state, so there's no `reset_sync` round-trip for
it:

```mermaid
sequenceDiagram
    participant U as User
    participant App as App (graphic/)
    participant BLE as services/ble_session.py
    participant FW as Firmware (comm_thread)
    participant Model as Firmware (model_thread)

    U->>App: Insert Food/Exercise Now…
    App->>App: SimulationEngine.add_instant_food/exercise() (local "expected" line)
    App->>BLE: queue_write("food_instant"/"exercise_instant", bytes)
    BLE->>FW: GATT write (one-shot)
    FW->>Model: model_thread_add_instant_food/exercise()
    Note over Model: decays over duration_min, summed/maxed<br/>with the recurring schedule each tick —<br/>no reset, no reset_sync notify
```

A *subsequent* reset from any other write (including **Stop**) does clear
these events along with everything else `apply_config_locked()` resets —
see `PROTOCOL_SPEC.md`'s "Instant food/exercise events" section for the
byte format, decay/delivery rules, and a bug this reset used to have.

On a multi-sensor board an instant event lands on whichever slot the
**Sensor select** cursor points at — the app writes the cursor first, then
the one-shot event.

**CGMS Only mode** (§7 below) is the other exception — enabling it never
resets either.

### 4.5 Board layout push (multi-sensor)

The **Board Layout** window (`graphic/board_layout_window.py`, opened from
Configuration → "Board layout") assigns a saved Person + Sensor profile to
each of the N slots and pushes the whole thing in one action.
`BleSession.send_board_layout()` runs it as a single coroutine over **one**
connection (any identity reaches the shared config service), paced so the
firmware's config queue keeps up:

```mermaid
sequenceDiagram
    participant U as User
    participant BLW as graphic/board_layout_window.py
    participant BLE as services/ble_session.py
    participant FW as Firmware (comm_thread)
    participant Model as Firmware (model_thread)

    U->>BLW: assign slots 0..N-1 → person / CSV, click "Send layout to Board"
    BLW->>BLW: _build_slots() — encode person/sensor/data_source/food/exercise per slot
    BLW->>BLE: send_board_layout(slots)
    loop for each assigned slot i
        BLE->>FW: write sensor_select = i
        BLE->>FW: write person, sensor, data_source, food(clear+N), exercise(clear+M)  (paced, retried on a full queue)
        opt person is CSV-backed
            BLE->>FW: CSV BEGIN / DATA×k / COMMIT  (into slots[i]'s flash track)
        end
        BLE-->>BLW: board_layout_progress(i+1, N)
    end
    BLE->>FW: write run_state = RUNNING
    FW->>Model: apply_config() per write — every slot reinits, sim_clock_min = 0
    BLE-->>BLW: board_layout_finished(ok)
```

Persisted app-side to `data/board_layout.json` (`models/board_layout.py`).
Verified end to end by `scripts/e2e_4sensor.py` (see
[`E2E_TEST_PLAN.md`](E2E_TEST_PLAN.md) §9).

## 5. Two independent clocks, deliberately

The board's `sim_clock_min` (in `model_thread.c`) and the app's
`SimulationEngine`'s own internal simulated-minutes counter (in
`models/engine.py`) are **two separate simulations of the same math**, not
one clock shared over BLE — there's no "tell me your current glucose"
round-trip in the hot path. On a multi-sensor board there is still exactly
**one** `sim_clock_min` and one speed multiplier — all N slots tick from it
together (see §9); the app's local engine runs for the one selected patient. Each side runs its own copy of the model
independently, ticking on its own local timer, and only synchronizes at two
discrete moments: when a config write resets both to t=0 (see
`PROTOCOL_SPEC.md`'s "Run state"/"Reset sync" sections), and never again
until the next reset. This is why sensor noise (present only on the board,
via the selected `SensorProfile`) is the only thing that should visibly
separate the "expected" (noiseless, local) and "received" (noisy, from the
board) lines on the graph — everything else about the two runs is the same
deterministic ODE integration from the same starting state.

## 6. Model Only mode

With `Model Only` checked (`main_window.py`), the app never opens a BLE
connection at all — the `SimulationEngine` becomes the sole data source, its
`expected_reading` signal driving both graphs directly instead of being
compared against board data. Existing for two reasons: (1) demoing/
developing the model math without hardware nearby, and (2) isolating "is
this a model bug" from "is this a BLE/firmware bug" when something looks
wrong — if Model Only also looks wrong, the bug is in `models/`, not in the
board.

## 7. CGMS Only mode

The opposite of Model Only: with `CGMS Only` checked, the board becomes a
**pure standard-CGM data source**. It streams nothing but CGM Measurement
notifications, suppresses Food/Exercise Status notifications, and refuses
every write to person/sensor/mode/food-event/exercise-event/food-instant/
exercise-instant — only Run state and the CGMS Only characteristic itself
stay writable. Existing to let the app (or any unmodified BLE CGM client)
exercise the standard CGM Service in isolation from the custom sim-config
service, as its own correctness check independent of the simulator UI.

Enabling never resets (whatever's running keeps running); disabling always
performs the same full reset a Run-state `STOPPED` write does, and — unlike
the board's normal "autonomous, defaults to running" behavior — requires an
explicit Start afterward rather than auto-resuming. See `PROTOCOL_SPEC.md`'s
"CGMS Only mode" section for the full write-rejection and transition rules.

```mermaid
sequenceDiagram
    participant U as User
    participant App as App (graphic/main_window.py)
    participant BLE as services/ble_session.py
    participant FW as Firmware (comm_thread)
    participant Model as Firmware (model_thread)

    U->>App: Check "CGMS Only"
    App->>App: lock Person/Sensor/Food/Exercise/Fast/Model-Only/<br/>Start-Pause-Stop/Insert-Now controls, discard local SimulationEngine
    App->>BLE: queue_write("cgms_only", 1)
    BLE->>FW: GATT write (CGMS Only char)
    FW->>Model: set_run_state(RUNNING) if not already running — no reset
    FW->>FW: enable write-rejection for person/sensor/mode/<br/>food/exercise/instant characteristics
    loop every measurement_interval (5 s)
        Model->>Model: step physiological model + sensor noise
        FW->>BLE: CGM Measurement notify only (Food/Exercise Status suppressed)
        BLE-->>App: new_message signal
        App->>App: append to "received" graph (no "expected" line)
    end

    U->>App: Uncheck "CGMS Only"
    App->>BLE: queue_write("cgms_only", 0)
    BLE->>FW: GATT write (CGMS Only char)
    FW->>Model: set_run_state(STOPPED) — full reset, then hold
    FW->>FW: disable write-rejection
    App->>App: unlock controls
    Note over U,App: Board now waits for an explicit Start — no auto-resume
```

## 8. Where each concern actually lives

| Concern | Owner |
|---|---|
| Physiological model math (ODEs) | `models/cgmsim_*.c` (firmware) and `models/*.py` (app) — same source, two ports |
| Sensor noise math | firmware only (`models/cgmsim_sensors.c`) — never modeled in the app; the app's "expected" line is deliberately noiseless |
| BLE wire format | `api/ble_uuids.py` + `api/protocol.py` (app) and `config_service.c` + `sim_config.h` (firmware) — both sides hand-kept in sync, see [`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md) |
| Config persistence (board) | firmware only — `sim_config.c` (v5, per-slot), external SPI-NOR flash; uploaded CSV tracks in `csv_store.c` (per slot) |
| Sensor count | build-time only — `CONFIG_APP_SENSOR_COUNT`; `sim_config_load_from_flash()` force-overrides the flash value so it always matches the running build |
| Profile persistence (app) | `models/profile_store.py` → `data/profiles.json`; slot→patient map in `models/board_layout.py` → `data/board_layout.json` |
| Per-identity demux | `services/ble_session.py` — parses the advertised name's trailing digit to a 0-based `_own_instance_index`, filters CGM Measurement + Food/Exercise Status to that slot, and sets `_require_pairing = False` for numbered identities |
| UI | `graphic/` (app only — firmware has no display beyond one status LED) |

## 9. Multi-sensor: N independent slots

`CONFIG_APP_SENSOR_COUNT` (1–4, default 4) sets how many fully independent
sensor slots the one board runs. Each slot has its own `struct sensor_slot`
in `sim_config` v5 — data source (model + params + noise + food/exercise
schedule) **or** an uploaded CSV, mixable (e.g. 3 CSV + 1 model). What is
**shared**: `sim_clock_min`, `speed_mult`, and the run state (one Start/Stop
for all — per-slot run state is `FEATURE_IDEAS.md` #19).

```
                          nRF54L15 DK (CONFIG_APP_SENSOR_COUNT = 4)
  ┌───────────────────────────────────────────────────────────────────────┐
  │  main.c:  identity 0..3  ─ adv set 0..3 ─ bt_cgms instance g_cgms[0..3]│
  │           "Nordic Glucose Sensor 1".."4"   (identity 0 = factory addr) │
  │                                                                        │
  │  model_thread:   rt[0]   rt[1]   rt[2]   rt[3]      ← per-slot model +  │
  │                    │       │       │       │          noise + schedule │
  │                    └───────┴───┬───┴───────┘                           │
  │                          sim_clock_min  (one, shared)  ── speed_mult   │
  │                                │                                       │
  │  comm_thread:  push loop over slots → bt_cgms_measurement_add(g_cgms[i])│
  │                + one Food/Exercise Status notify per slot (u8 slot)     │
  │                config queue: per-sensor writes → sim_config.slots[sel]  │
  │  config_service:  ONE sim-config service; "Sensor select" (5b2c0015)   │
  │                   picks `sel` for per-sensor reads/writes              │
  └───────────────────────────────────────────────────────────────────────┘
        ▲ BLE identity i ── one connection per identity ── app: one BleSession each
```

**App.** Connect to each identity's address (Bluetooth window) → one
`BleSession` per identity → one tree row per identity. Each session shows
only its own slot's CGM Measurement + Food/Exercise Status (demux by the
advertised-name digit); the graph plots the selected row. The **Board
Layout** window assigns slot → person/CSV and pushes the whole layout
(§4.5). Once a slot is assigned, the Bluetooth list, the tree row, and the
config windows' "Target device" combo show that **patient's name** instead of
"Nordic Glucose Sensor N" (`models/board_layout.device_label` /
`session_name`; the advertised name still drives the demux + pairing). The
config windows and the Insert-Now dialogs also gain a **"Slot"** picker
(`DeviceTargetBar` / `_InstantDialog`) so one slot can be reconfigured or
fed a one-shot event without re-pushing the whole layout.

**Pairing.** Windows aborts LE Secure Connections against the board's
non-default identities (confirmed on hardware — see
[`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md) §7 and the `ble-pairing-issue`
memory). So `N > 1` builds set `CONFIG_APP_CGMS_NO_AUTH` — the CGMS
characteristics drop the authenticated-link requirement and all N identities
stream **unpaired** (acceptable for a simulator; see the
`cgms-no-auth-tradeoff` memory). `N == 1` keeps real pairing + encryption.

**Verification.** `scripts/e2e_4sensor.py` — 11 hardware cases: layout push,
per-slot readback, per-identity demux, CSV slot playback, shared fast-mode
clock, slot-targeted Insert Food/Exercise/PISA, range alerts, per-slot
config isolation, reconnect autonomy, reboot persistence. See
[`E2E_TEST_PLAN.md`](E2E_TEST_PLAN.md) §9.
