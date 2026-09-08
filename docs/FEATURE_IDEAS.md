# Feature ideas / roadmap

Candidate features for SensorSimulator, beyond what's already built. Not
commitments — a menu to pick from. Ordered roughly by value-for-effort for the
thesis.

**Legend** — Effort: S ≤ 1 day · M ≈ 2–4 days · L ≈ 1–2 weeks · XL > 2 weeks.
Risk: hardware/protocol changes and anything touching pairing are higher risk.

| # | Feature | Effort | Risk | Depends on |
|---|---|---|---|---|
| 1 | Clinical CGM metrics (GMI, CV, MAGE, TIR/TAR/TBR, mean, SD) on **both** lines + export | M | low | — |
| 2 | Model-line LOW/HIGH alerts + deep-red (`tbr2`/`tar2`) marker + toast/sound | S | low | — |
| 3 | Session record & replay (capture a whole app run → file → re-play offline) | M | low | metric export |
| 4 | PDF / HTML results report for the thesis (graphs + metrics + config) | M | low | #1, #3 |
| 5 | Multi-sensor on one board — N CGMS instances on **one** identity | L | med | ble_session already demuxes |
| 6 | Arbitrary CSV window length + "jump to sim-time" scrubber | M | low | CSV playback (done) |
| 7 | Historical record retrieval over RACP (`0x2AAC`) | M | low | — |
| 8 | Insulin input: bolus events + basal profile (currently constant basal only) | L | med | model changes |
| 9 | Sensor warm-up / calibration event simulation | M | low | instant-event pattern |
| 10 | Noise-model tuning UI with live expected-vs-received overlay | M | low | — |
| 11 | Auto-reconnect + connection-health indicator | S | low | — |
| 12 | Config presets library + import/export profiles | S | low | profile_store |
| 13 | Firmware DFU / OTA update from the app | L | med | MCUboot |
| 14 | pt-BR localization (thesis is Brazilian) | M | low | — |
| 15 | Remote/web dashboard (read-only live view) | L | med | — |
| 16 | Dexcom-proprietary BLE compatibility mode | XL | high | BLE_PAYLOAD_VALIDATION.md — **partial done 2026-09** (configurable profile + unauthenticated stream; auth handshake still open) |
| 17 | Scheduled scenario runner (unattended demo sequences) | S | low | **done 2026-09** (`models/scenario.py`, `scenarios/*.json`, Scenario window) |
| 18 | Fault injection panel (dropouts, spikes, stuck sensor, compression low) | M | low | **scaffold done 2026-09** — panel + `MainWindow.inject_fault`, PISA wired; other faults TODO |

---

## Details

### 1. Clinical CGM metrics on both lines + export  *(TODO #1)*
**Problem.** The range-metrics panel shows a few numbers for the current view
only; the thesis results chapter needs standard CGM metrics computed
consistently over the **expected** and **received** series and over a chosen
interval, plus a way to get them out.
**Sketch.** Extend `models/cgm_metrics.py` with GMI (`3.31 + 0.02392·mean`), CV
(`SD/mean·100`), MAGE, and per-series computation; a "Metrics" window that shows
expected vs received side by side for the whole run (not just the visible
window) and a "Copy / Save CSV" button. Reuse the thresholds from
`app_settings`.
**Touches.** `models/cgm_metrics.py`, a new `graphic/metrics_window.py`,
`main_window` toolbar button. **Value.** Directly feeds the thesis.

### 2. Model-line LOW/HIGH alerts + deep-red marker  *(gap found 2026-09)*
**Problem.** `_update_user_alert()` only fires on the **BLE** path
(`_on_new_message`), so Model Only / expected-line excursions show no badge; and
it only distinguishes one level (`tbr1`/`tar1`), not the deep-red `tbr2`/`tar2`
zone.
**Sketch.** Call an alert updater from `_on_expected_reading` too (a synthetic
"Model" tree row or a status label under the graph); add `▼▼ VERY LOW` / `▲▲
VERY HIGH` for the `tbr2`/`tar2` crossings; optional `QSystemTrayIcon` toast +
short beep, debounced. Also annotate the graph at the crossing.
**Touches.** `main_window.py` (`_update_user_alert`, `_on_expected_reading`,
`_build_stats_panel`). **Value.** Makes the false-low from PISA actually
*visible* as an alert — the point of the PISA feature.

### 3. Session record & replay
**Problem.** Demos and thesis figures need reproducibility; right now a run is
gone when the app closes.
**Sketch.** A recorder that appends every `expected_reading`, every BLE message,
and every user action (config sends, instant events, speed changes) to a
`.ssrec` (JSONL) file with timestamps. A replay mode that feeds those back into
the graphs/metrics at original or accelerated rate, with no board or engine.
**Touches.** new `models/session_record.py`, `main_window` (record toggle,
"Open recording"). **Value.** Every figure in the thesis becomes regenerable;
also a great offline test fixture for the e2e harness.

### 4. Results report (PDF/HTML)
**Problem.** Assembling thesis figures by hand is slow and inconsistent.
**Sketch.** "Export report" produces a single HTML (and/or PDF via matplotlib +
a print stylesheet) containing: the config used (person/sensor/events/speed/data
source), the glucose + food/exercise graphs, the metrics table (#1), and the
list of injected events with timestamps. Works from a live run or a recording
(#3).
**Touches.** new `graphic/report.py`, reuses the figure builders. **Value.**
High for thesis write-up.

### 5. Multi-sensor on one board — N CGMS instances, one identity  *(deferred item in TODO)*
**Problem.** The assignment's "several sensors" story; the N-BLE-identity path is
blocked by the Windows LE-SC pairing bug.
**Sketch.** `CONFIG_BT_CGMS_INSTANCE_COUNT = N`; call `bt_cgms_init()` N times;
`model_thread`/`comm_thread` iterate N slots, each with its own
person/sensor/data_source/schedule/instant slots. Config characteristics gain a
leading `u8 sensor_index`. App: the tree already renders one row per instance
(`ble_session._instance_by_handle`), add per-instance config targeting.
**Touches.** firmware `main.c`, `model_thread.*`, `comm_thread.*`,
`config_service.c`, `sim_config.h` (arrayed); app `protocol.py`,
`ble_session.py`, the config windows. **Risk.** Struct size × N vs the 4 KB
partition; RAM for N model unions. **Value.** Closes the original 4-sensor
design cleanly, no pairing bug.

### 6. Arbitrary CSV window + time scrubber
**Problem.** CSV playback is fixed at a 24 h window from a chosen start; useful
to replay a shorter segment or seek within it during a demo.
**Sketch.** Let CSV Analysis pick any `[start, end]`; `build_glucose_track`
already handles any length. Add a "sim-time" slider on the main window (CSV
mode only) that writes a new `SOCP`-style seek or re-sends `run_state` with an
offset — or simpler, an app-only seek of the local replay plus a board
`data_source` re-arm. **Touches.** `csv_analysis_window`, `engine._run_csv`,
maybe a `CSV seek` opcode. **Value.** Nicer live demos.

### 7. Historical record retrieval (RACP)
**Problem.** The CGMS library already stores up to 20 measurement records
(`CONFIG_BT_CGMS_MAX_MEASUREMENT_RECORD`); the app never queries them.
**Sketch.** Implement RACP report/count/delete on the app side
(`0x2AAC` write + indicate), a "History" panel that pulls the last N records
after a reconnect and back-fills the graph. **Touches.** `ble_session.py`
(indicate handling), a new panel. **Value.** Demonstrates the full standard
CGMS profile, not just live notify — good thesis talking point.

### 8. Insulin input (bolus + basal profile)
**Problem.** Models run at a single constant steady-state basal; no bolus, so
meal responses are unrealistically prolonged.
**Sketch.** Add a bolus instant event (`u16 duration; f32 units`) and a
time-of-day basal profile in `sim_config`; feed into each model's insulin input
(`iir` / `u1b` / `IIRb`). **Touches.** all four `models/*.py` + `cgmsim_*.c`,
`sim_config`, new characteristics, app UI. **Risk.** Model re-validation vs
`cgmsim` reference. **Value.** Makes meal scenarios credible; sizeable.

### 9. Sensor warm-up / calibration simulation
**Sketch.** On session start (or an injected "new sensor" event), suppress or
widen noise for a configurable warm-up period, then require a calibration entry
before values are "trusted" (CGM Status warning bits). Reuses the instant-event
+ status-annunciation machinery. **Value.** Realistic sensor lifecycle;
moderate effort.

### 10. Noise-model tuning UI
**Sketch.** A window with live sliders for Breton/Facchinetti params that
re-send sensor config and show the expected (clean) vs received (noisy) overlay
and the resulting CV/MARD. **Touches.** `sensor_config_window`, a small live
plot. **Value.** Turns sensor-noise into something you can *show*, not just
configure.

### 11. Auto-reconnect + health indicator
**Sketch.** On `disconnected`, `BleSession` retries with backoff; a toolbar
dot shows connected / reconnecting / lost; suppress the "frozen value" trap by
greying the line when stale. **Touches.** `ble_session.py`, `bluetooth_window`,
`main_window` status. **Value.** Removes the most common demo annoyance.

### 12. Config presets + import/export
**Sketch.** Ship a few named presets (healthy, T1D poorly controlled, T2D,
exercise study); `profile_store` gains import/export of a single JSON bundle
(persons + sensors + events + data-source). **Value.** Faster setup, shareable
scenarios.

### 13. Firmware DFU / OTA from the app
**Sketch.** Enable MCUboot + the SMP/DFU service; app uploads a signed image
over BLE. **Risk.** Bootloader partitioning, image signing, bricking. **Value.**
Convenience only — the J-Link flow works fine for a thesis.

### 14. pt-BR localization
**Sketch.** Wrap UI strings in `tr()` / a small dict, ship a pt-BR resource,
language toggle in the View window. **Value.** The thesis and defense are in
Portuguese; screenshots in pt-BR read better.

### 15. Remote/web dashboard
**Sketch.** A tiny local HTTP/WebSocket server in the app publishes the live
expected/received series + metrics; a static page renders them. Read-only.
**Value.** Nice for showing the running sim on a projector without the PyQt
window; medium effort.

### 16. Dexcom-proprietary BLE compatibility + configurable comm profile  *(now tracked in TODO)*
**Sketch.** Per `BLE_PAYLOAD_VALIDATION.md`: a second firmware GATT service
(`FEBC` / `F8083532-…`), J-PAKE auth, opcode-tagged glucose/backfill messages,
so xDrip/Loop-class clients read the board as a "real" Dexcom — **and** a
persisted `sim_config.comm_profile` (`0` SIG CGMS / `1` Dexcom) plus a
"Communication type" combo in the app that switches which profile the board
advertises and how `ble_session.py` parses it. One profile active at a time.
**Risk/Effort.** High/XL — reverse-engineered, encrypted handshake, easy to get
subtly wrong; keep behind the flag with SIG CGMS as default. **Value.**
Interoperability demo; only worth it if the thesis calls it out.

### 17. Scheduled scenario runner  *(done 2026-09)*
JSON files of timed actions (`scenarios/*.json`) fired on a wall-clock timeline
from the **Scenario** window (`models/scenario.py` +
`graphic/scenario_window.py`); `MainWindow._scenario_dispatch` maps `kind` →
speed / run_state / person / data_source / comm_profile / insert_food /
insert_exercise / inject_fault. Shipped: `demo_pisa`, `cambridge_meal`,
`deichmann_exercise`, `royparker_exercise`, `alerts_low_high`, `speed_sweep`,
`csv_playback`, `full_demo`. `scripts/e2e.py` S17 replays one as a test.
**Next.** A `preset` action (needs #12), sim-time-based schedule option.

### 18. Fault-injection panel  *(scaffold done 2026-09)*
`graphic/fault_panel.py`'s `FAULTS` registry + `MainWindow.inject_fault(kind,
values)` + a "Faults" toolbar window. **PISA** (compression low) is the one
wired fault. **Next.** Signal dropout (no notify for N min), pressure spike,
stuck sensor (value frozen), Dexcom "???" gap — each = a `FAULTS` entry + a
firmware slot like `instant_pisa[]` + its own graph-shade colour. Turning the
simulator into a proper *fault* simulator is a strong thesis contribution.

---

## Suggested near-term order (for the thesis)

1. **#2** model-line alerts (tiny, closes a visible gap).
2. **#1** clinical metrics on both lines + CSV export.
3. **#3** session record/replay → **#4** results report.
4. **#18** more fault types (dropout / spike / stuck) — the panel already exists.
5. Optional: **#5** multi-sensor (completeness) or finishing **#16**'s auth
   handshake, depending on what the thesis emphasizes.

Done this cycle: #3 (CSV playback), #16 (partial), #17, #18 (scaffold),
plus PISA (#2/#4 firmware), the x1–x1000 speed multiplier, the rolling
graph window, and the `scripts/e2e.py` E2E harness.
