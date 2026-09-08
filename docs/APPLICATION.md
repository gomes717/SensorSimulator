# Application Architecture

The desktop app (`src/`, PyQt6) is organized into six layers reflecting the
`api → services → core → models → gui` (+ `utils`) directory split — see
[`ARCHITECTURE.md`](ARCHITECTURE.md) for how this fits into the whole
system.

```
src/
├── main.py         entry point
├── api/            BLE wire-format contract (no I/O, no Qt)
├── services/       active BLE I/O — connections, scanning, pairing
├── core/           shared cross-cutting state (the message log)
├── models/          physiological simulation + profile persistence (no Qt widgets)
├── gui/         every window/dialog — the UI layer
└── utils/          reserved for generic helpers (currently empty)
```

The dependency direction is one-way: `gui/` depends on everything below
it; `services/` depends on `api/`; `models/` and `api/` depend on nothing
else in the project. Nothing in `api/`, `services/`, `core/`, or `models/`
imports from `gui/` — the backend has no idea the UI exists, which is
what makes `models/` reusable standalone (`cgmsim/` is the CLI-only sibling
of the same model math) and lets Model Only mode run the full simulation
with zero BLE/UI code in the hot path.

## 1. Threading model

Everything that can block — a BLE operation, or a real-time simulation tick
— runs on its own `QThread`, never the GUI thread, communicating back via
Qt's signal/slot mechanism (which is thread-safe by construction: a signal
emitted from a worker thread and connected to a slot living on the GUI
thread is automatically delivered as a **queued** call, executed the next
time the GUI thread's event loop is free — no manual locking needed on the
receiving end).

| Thread | Class | File | Talks to GUI via |
|---|---|---|---|
| One per connected device | `BleSession` | `services/ble_session.py` | `new_message`, `config_read`, `write_failed`, `reset_sync` signals |
| One while scanning | `BluetoothScanThread` | `services/bluetooth_scanner.py` | device-found signal |
| One per active run | `SimulationEngine` | `models/engine.py` | `expected_reading` signal |

`BleSession` additionally owns **its own asyncio event loop** inside its
`run()` — `bleak` (the BLE library) is asyncio-native, so bridging it into
Qt means: the GUI thread calls thread-safe entry points
(`queue_write()`, `request_read()`), which use
`loop.call_soon_threadsafe()` / `asyncio.run_coroutine_threadsafe()` to hand
work to that session's asyncio loop; results come back out via the signals
above. Every connected device gets its own `BleSession`/asyncio loop —
`bluetooth_window.py`'s `sessions()` dict is the registry.

`SimulationEngine` (see [`MODELS.md`](MODELS.md) §7 for what it computes)
is a plain polling loop (`k_sleep`-equivalent: `self.msleep(...)`), one tick
per wall-clock second, independent of any BLE activity — it's the Python-side
twin of the firmware's `model_thread`, and deliberately has *no* dependency
on whether a board is even connected.

## 2. Backend layers

### 2.1 `api/` — the BLE contract

- **`ble_uuids.py`** — every custom 128-bit characteristic UUID, as plain
  string constants. Must byte-for-byte match `config_service.c`'s
  `BT_UUID_128_ENCODE(...)` calls — there is no shared source of truth
  beyond both sides being hand-kept in sync (see
  [`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md)).
- **`protocol.py`** — `encode_*`/`decode_*` functions: Python `struct.pack`/
  `unpack` calls producing the exact little-endian byte layouts the
  firmware's C structs expect (`__packed`, no compiler padding). Each
  `encode_*` has a matching firmware-side parse and, where the
  characteristic is readable, a `decode_*` that's an exact inverse — this
  is verified by the "Read from Board" round-trip in every config window.

Neither file imports Qt or does any I/O — they're pure data transformation,
which is why `models/` (the physiological math) can safely import from
`api/` (`protocol.py` needs `ModelId`/parameter name lists from `models/`)
without creating a dependency on the GUI or BLE stack.

### 2.2 `services/` — active BLE I/O

- **`ble_session.py`** — `BleSession`, the persistent per-device connection
  (see §1). Also home to `_sfloat_to_float()` (decoding the standard CGMS
  Measurement's IEEE-11073 SFLOAT glucose value) and the custom
  config-service characteristic discovery/dispatch table
  (`CONFIG_CHAR_KEY_BY_UUID`, incl. `"sensor_select"`).
  **Multi-sensor:** a numbered advertised name ("Nordic Glucose Sensor 3")
  sets `_own_instance_index` / `slot_index` (0-based) so the session shows only
  that slot's CGM Measurement + Food/Exercise Status, and `_require_pairing =
  False` so it skips the Windows pairing step (Option-A firmware needs none, and
  attempting it wedges the WinRT stack). An optional `display_name` (the
  assigned patient — see `board_layout`) is what `_user_id()` shows on the tree
  / graph, without disturbing any name parsing. `request_read()` rides the same
  FIFO as `queue_write()`, so "set the sensor-select cursor, then read that
  slot" stays ordered. `send_board_layout(slots)` runs the whole multi-slot push
  as one coroutine (cursor + per-slot config + optional per-slot CSV upload,
  paced and retried).
- **`bluetooth_scanner.py`** — `BluetoothScanThread`, a short-lived BLE
  discovery scan (via `bleak.BleakScanner`), feeding `bluetooth_window.py`'s
  device list.
- **`windows_ble_pairing.py`** — Windows-only, `winrt`-based programmatic
  pairing (typing the fixed test passkey automatically instead of needing
  the OS's own pairing prompt); local-imported from `ble_session.py` only
  when actually pairing, so the `winrt` dependency isn't required on other
  platforms.

### 2.3 `core/` — shared state

- **`ble_message_log.py`** — `BleMessageLog`, a single `QObject` with one
  `new_message` signal, instantiated once by `MainWindow` and passed to
  every window that needs BLE traffic: `main_window.py` (graphs/treeview),
  `debug_window.py`/`message_detail_window.py` (raw inspection). This is
  the app's internal pub/sub bus — `BleSession` instances feed it, an
  arbitrary number of windows subscribe to it, and none of those windows
  need to know about each other or about `BleSession` directly.

### 2.4 `models/` — physiological simulation (see [`MODELS.md`](MODELS.md))

- `types.py` — the plain-data contract (`PersonProfile`, `SensorProfile`,
  `FoodEvent`, `ExerciseEvent`, `ModelId`, `SensorId`) shared by everything
  above it.
- `{cambridge,uva_padova,royparker,deichmann}.py`, `sensors.py` — the model
  math itself, ported verbatim from `cgmsim/src/cgmsim_*.c`.
- `engine.py` — `SimulationEngine` (§1).
- `profile_store.py` — JSON persistence of every saved `PersonProfile`/
  `SensorProfile` to `data/profiles.json` (`dataclasses.asdict` + `json`,
  no external serialization library).
- `board_layout.py` — the slot → (person, sensor) map for a multi-sensor
  board (`BoardLayout` / `SlotAssignment`), persisted to
  `data/board_layout.json`; applied by `BleSession.send_board_layout()`.
- `app_settings.py` — same style, for app-wide settings in
  `data/settings.json` (glucose range thresholds, speed multiplier, rolling
  view window, theme).
- `food_log_csv.py` — reader for D1NAMO-style food-log CSVs, auto-paired to
  a Dexcom export by file id (`Dexcom_001` ↔ `Food_Log_001`).
- `scenario.py` — timed-action scenario files (`scenarios/*.json`) fired on
  a wall-clock timeline by the Scenario window.
- `cgm_metrics.py` — pure `compute()` of the clinical range metrics
  (TIR/TBR1/TBR2/TAR1/TAR2, mean, population variance, SD, CV) for a list
  of glucose values; shared by the CSV Analysis window and the main
  window's live metrics panel. TIR/TBR/TAR are reported as **time in each
  band** (`*_min` fields, rendered `h:mm` by `fmt_hm`): pass `span_minutes`
  (the CSV window's real duration, or the live view's wall-clock span). The
  `*_pct` fractions are kept underneath.
- `dexcom_csv.py` — pure stdlib reader for Dexcom Clarity CGM exports
  (`dataset/Dexcom_*.csv`), returning the EGV `(timestamp, glucose)` rows.

## 3. UI layer (`gui/`)

`MainWindow` is the hub; every other window is created lazily (on first
open, via `_open_*` methods) and kept as a `None`-until-opened attribute —
so launching the app never pays for windows the user never opens, and
re-opening one raises the same instance instead of creating a duplicate.

| Window/dialog | Role |
|---|---|
| `main_window.py` — `MainWindow` | Toolbar (Configuration, CSV Analysis, Bluetooth, Debug, View, Scenario, Faults); user treeview (one row per connected identity — per-user `#id` + generated avatar disc, live glucose, LOW/HIGH badge beside the name when out of range); glucose graph (received solid + expected dashed, TBR2/TBR1/TIR/TAR1/TAR2 range shading, mean line, PISA-shaded intervals, rolling view window); food/exercise graph (carb rate + exercise %); Start/Pause/Resume/Stop; Insert Food/Exercise/PISA Now buttons; live range-metrics panel |
| `configuration_window.py` — `ConfigurationWindow` | The Person/Sensor selectors + their Configure/Food/Exercise buttons, a **"Board layout (4 sensors)…"** button, the Speed slider (x1–x1000) / Communication-type combo / Model-Only / CGMS-Only toggles, the editable glucose range thresholds, and the per-person data-source choice (physiological model vs CSV region — CSV playback **is** wired, incl. "Send CSV to Board") |
| `board_layout_window.py` — `BoardLayoutWindow` | Multi-sensor: a Person + Sensor-noise combo per slot (persisted to `data/board_layout.json`), a target-board picker, and **"Send layout to Board"** → `BleSession.send_board_layout()` |
| `view_config_window.py` — `ViewConfigWindow` | Rolling graph-window length and UI theme |
| `scenario_window.py` — `ScenarioWindow` | Pick a `scenarios/*.json` file and run its timed action list against the board/engine |
| `fault_panel.py` — `FaultPanel` | The `FAULTS` registry window; each row opens a dialog → `MainWindow.inject_fault()` (PISA wired) |
| `csv_analysis_window.py` — `CsvAnalysisWindow` | Load a Dexcom CGM export (`models/dexcom_csv.py`), zoom/pan the full trace, slide a 24 h window over it, and read range metrics (`models/cgm_metrics.py`: TIR/TBR/TAR as time, mean, variance, SD, CV) — two panels side by side: **whole recording** and the **selected 24 h window**. Analysis only — does not feed the live simulation |
| `bluetooth_window.py` — `BluetoothWindow` | Device list, scan trigger, multi-device connect/disconnect; owns the `sessions()` dict every other window resolves a "target device" through. Shows `"<patient> — Sensor N"` for a slot assigned in `board_layout.json` (raw `"Nordic Glucose Sensor N"` otherwise); `relabel()` refreshes those live after a Board Layout edit |
| `device_target.py` — `DeviceTargetBar` | "Target device: [combo] [Slot] [Refresh]" shared by the four config windows, plus the `restart_board()` / `await_send_confirmation()` helpers they all call after a write. The **Slot** combo shows only for a numbered multi-sensor identity; `.begin()` queues the `sensor_select` cursor before the window's own write/read |
| `debug_window.py` — `DebugWindow` | Live scrolling list of every BLE message received (any device), sourced from `core/ble_message_log.py` |
| `message_detail_window.py` — `MessageDetailWindow` | Full field dump of one selected message from Debug |
| `person_config_window.py` — `PersonConfigWindow` | Manage saved `PersonProfile`s (model choice + its parameters), Save/Send to Board/Read from Board |
| `sensor_config_window.py` — `SensorConfigWindow` | Manage saved `SensorProfile`s (noise model + parameters), same Save/Send/Read pattern |
| `food_config_window.py` — `FoodConfigWindow` | Recurring-daily meal schedule (table + add row) for the active person |
| `exercise_config_window.py` — `ExerciseConfigWindow` | Recurring-daily exercise schedule, same pattern |
| `instant_event_dialog.py` — `FoodInstantDialog`/`ExerciseInstantDialog` | One-shot "insert now" prompts, invoked from `MainWindow`, not tied to a saved profile |

The four config windows (`Person`/`Sensor`/`Food`/`Exercise`) all share one
shape — table or form + Save (local) / Send to Board (BLE write) / Read from
Board (BLE read, round-trips through `protocol.py`'s `decode_*`) — which is
why `device_target.py` factors out exactly the pieces that differ from
window to window: which device to target, and how to show "did that write
land" feedback.

## 4. Data flow examples

**User edits and sends a Person config:**
`person_config_window.py` (edits `PersonProfile` in memory) → Save
(`profile_store.save()`, writes `data/profiles.json`) → Send to Board
(`api.protocol.encode_person_config()` → `services.ble_session.BleSession.queue_write()`
→ GATT write → firmware) → `await_send_confirmation()` waits for the
board's `reset_sync` notification to show "✓ Applied on board".

**A CGM reading arrives:**
firmware notify → `BleSession`'s notification handler (asyncio callback) →
(multi-sensor: dropped unless it's this identity's own CGMS instance) →
decodes the standard CGMS SFLOAT payload → emits on `core.BleMessageLog`'s
`new_message` signal (queued onto the GUI thread) → `MainWindow._on_new_message()`
updates the treeview row (`_update_user_alert()` badges it LOW/HIGH vs the
current thresholds) and, if that device is selected and a run is active,
appends to the glucose graph.

**A multi-sensor board layout is pushed:**
`board_layout_window.py` (`BoardLayout` in memory, saved to
`data/board_layout.json`) → `_build_slots()` encodes each slot's
person/sensor/data-source/food/exercise (+ CSV tracks when the person is
CSV-backed) → `BleSession.send_board_layout(slots)` runs one coroutine:
per slot, write `sensor_select` then the per-slot writes (paced ~80 ms,
retried on a full config queue) then any CSV upload, finally `run_state =
RUNNING` → `board_layout_progress` / `board_layout_finished` signals drive
the window's status label. See [`ARCHITECTURE.md`](ARCHITECTURE.md) §4.5.

**A run starts:** `MainWindow._start_run()` (re)creates a `SimulationEngine`
for the active person, anchors the graph's t=0 to now, and writes
`RUN_STATE_STOPPED` then `RUN_STATE_RUNNING` to every connected board — the
local engine and every connected board's `model_thread` begin ticking from
the same nominal t=0, independently (see [`ARCHITECTURE.md`](ARCHITECTURE.md)
§5 for why they're not kept in lockstep beyond that shared starting point).
