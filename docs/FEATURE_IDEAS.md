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
| 5 | Multi-sensor on one board — N independent slots. Phase 1 (firmware) + Phase 2 (app Board Layout window) **done + HW-verified** 2026-09; streams unpaired via `CONFIG_APP_CGMS_NO_AUTH` | L | med | ble_session already demuxes |
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
| 16 | Dexcom-proprietary BLE compatibility mode | XL | high | BLE_PAYLOAD_VALIDATION.md — **partial done 2026-09** (configurable profile + unauthenticated stream; auth handshake still open; single-sensor only) |
| 17 | Scheduled scenario runner (unattended demo sequences) | S | low | **done 2026-09** (`models/scenario.py`, `scenarios/*.json`, Scenario window) |
| 18 | Fault injection panel (dropouts, spikes, stuck sensor, compression low) | M | low | **scaffold done 2026-09** — panel + `MainWindow.inject_fault`, PISA wired; other faults TODO |
| 19 | Per-slot run / pause / stop (currently one global run state for all 4 slots) | M | med | #5, `sensor_select` cursor |
| 20 | Multi-slot graph overlay + "which slot is which patient" legend | M | low | #5 |
| 21 | Speed as a live scalar — a speed write shouldn't reset the sim clock / instant events | S | low | firmware `model_thread` |
| 22 | "Target slot" selector in the config windows + patient names in the UI — **done + HW-verified 2026-09** | S | low | #5 Phase 2 |
| 23 | MARD + Clarke / Consensus Error Grid (expected vs received) | M | low | #1 |
| 24 | Sensor-noise-model comparison sweep (batch: Ideal/Breton/Facchinetti → MARD/CV/grid table) | M | low | #23, scenario runner |
| 25 | Deterministic noise RNG seed (per-run reproducibility) | S | low | #3 |

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

### 5. Multi-sensor on one board — N independent slots  *(Phase 1 + 2 done, HW-verified — 2026-09)*
**Done (Phase 1, firmware).** N-BLE-identity design restored under
`CONFIG_APP_SENSOR_COUNT` (1–4, default 4); `N == 1` byte-identical to the
single-sensor build. `sim_config` v5 = `sensor_count`/`comm_profile`/
`speed_mult` + `slots[4]` of fully independent `struct sensor_slot` (model or
CSV, mixable); shared clock + speed. New **Sensor select** char (`5b2c0015`)
picks the slot per-sensor writes/reads hit — no leading index byte on the
existing payloads. `main.c` N identities/adv sets/CGMS instances,
`model_thread` per-slot `rt[]`/`latest[]`, `comm_thread` per-slot push,
`csv_store` 4 tracks. Pairing: **Option B** (`BT_PRIVACY=n` + static addrs + `MAX_PAIRED=8` +
peripheral-initiated security) **failed on hardware** — identities 1-3 hit
`Security failed … err 2` (AUTH_REQUIREMENT), Windows drops the link.
**Option A** is active: `CONFIG_APP_CGMS_NO_AUTH` (Kconfig `default y if
APP_SENSOR_COUNT > 1`) drops the CGMS auth requirement; all 4 identities
stream unpaired — **verified on hardware** (97/99/101/103 mg/dL, one per slot).
Builds `=1` (real security) and `=4`, RAM 47 %. App Phase 1:
`SENSOR_SELECT_UUID`, `encode/decode_sensor_select`, per-slot Food/Exercise
Status (`u8 slot` prefix), trailing-digit → 0-based `_own_instance_index`. See
`PROTOCOL_SPEC.md` §7.
**Done (Phase 2, app — HW-verified 2026-09).** `graphic/board_layout_window.py`
(opened from Configuration → "Board layout"): a Person + Sensor combo per slot,
persisted to `data/board_layout.json` (`models/board_layout.py`). "Send layout
to Board" → `BleSession.send_board_layout(slots)`: per slot, writes Sensor
select then `person`/`sensor`/`data_source`/food+exercise (paced ~80 ms, retried
on a full config queue), then a per-slot CSV upload when the person is
CSV-backed, then run state RUNNING. `_require_pairing = False` for numbered
identities so `BleSession` skips the Windows pairing step (attempting it wedged
the BLE stack). The graph plots the selected tree row; connecting to all 4
identities gives 4 rows.

**Done (E2E — 2026-09).** `scripts/e2e_4sensor.py`, 11 cases HW-verified
(`docs/E2E_TEST_PLAN.md` §9): layout push + per-slot readback, per-identity
demux, CSV slot playback, fast-mode shared clock, slot-targeted Insert
Food/Exercise/PISA, range alerts, per-slot config isolation, reconnect
autonomy, reboot persistence. Firmware hardened for the layout-push burst
(`CFG_MSGQ_DEPTH` 32, short CGMS retry) and `sensor_count` is now forced to the
build value on flash load.

**Not done / follow-ups.** Per-slot run/pause/stop (#19 — run state is still
global), a multi-slot graph overlay + legend (#20), a "target slot" in the
per-config windows (#22), sending one layout to several boards at once, and
**authenticated** multi-sensor links (blocked on the Windows LE-SC bug — a
dedicated non-Windows host BLE adapter, e.g. an nRF52840 dongle, is the likely
path if the thesis needs to claim secure multi-sensor).

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

### 19. Per-slot run / pause / stop  *(gap — user asked 2026-09)*
**Problem.** The 4-sensor board has one global `run_state` and one
`sim_clock_min`; Start/Stop hits all four slots together. There's no way to run
one sensor while the others hold.
**Sketch.** `run_state[MAX_SIM_SENSORS]` in `model_thread`, addressed by the
existing **Sensor select** cursor (`sensor_select=2` then `run_state=STOPPED`
stops only slot 2). Gate each slot inside the `tick_slot()` loop instead of
gating the whole `model_tick()`. Keep `sim_clock_min` shared — a stopped slot
just freezes its own model output while the global clock runs; caveat: a slot
resumed after a long stop sees its recurring daily schedule at the advanced
wall-time (may skip a meal window). App: a per-slot Start/Stop in the Board
Layout window (2 writes each: `sensor_select` + `run_state`).
**Touches.** `model_thread.{c,h}`, `comm_thread.c` (route `run_state` per slot),
`graphic/board_layout_window.py`. **Effort.** ~40–60 firmware lines + a few app.
**Value.** "Bring sensor N online mid-run" demos; realistic staggered sensor
starts.

### 20. Multi-slot graph overlay + patient legend  *(from #5 "not done")*
**Problem.** The main glucose graph shows only the selected tree row. With 4
independent sensors you want to see them together and know which line is which
patient/CSV.
**Sketch.** An "overlay all slots" toggle: plot each connected identity's
received line in its own colour, with a legend built from the Board Layout
(`slot i → person/CSV name`). Keep the selected line bold + its expected/PISA
overlays; dim the rest. Reuse `avatar_icon`'s colour hash for stable per-slot
colours. **Touches.** `main_window.py` (`_redraw_graph`, a per-user line dict),
`board_layout` for names. **Value.** The headline "4 sensors, 4 patients, one
board" figure for the thesis.

### 21. Speed as a live scalar (no sim reset)  *(wart found 2026-09)*
**Problem.** A **Speed** write (`5b2c0012`) goes through `apply_config_locked()`
— it resets `sim_clock_min` to 0 and clears the per-slot instant-event arrays,
same as a person/sensor write. Changing playback speed shouldn't disturb the
running simulation. `e2e_4sensor.py` works around it with `set_speed_settle()`.
**Sketch.** `model_tick()` reads a mutex-guarded `static float live_speed_mult`
instead of `active_cfg.speed_mult`; `comm_thread`'s speed handler updates that
directly (early-return, like the instant events) and still persists to flash —
no `model_thread_apply_config()` call, no `reset_sync`. `DATA_SOURCE` /
`COMM_PROFILE` could get the same treatment where a reset isn't actually needed.
**Touches.** `model_thread.{c,h}`, `comm_thread.c`, drop the settle in the E2E.
**Effort.** S. **Value.** Removes a real gotcha; makes speed changes feel live.

### 22. "Target slot" selector + patient names in the UI  *(done + HW-verified 2026-09)*
**Done.** `DeviceTargetBar` (`graphic/device_target.py`) gained a **"Slot: [0–3]"**
combo, shown only when the target is a numbered multi-sensor identity
(`BleSession.slot_index is not None` — a single-sensor board also carries the
`sensor_select` char, so that alone isn't the signal). `.begin()` (used by all
four `*_config_window.py` "Send to Board" / "Read from Board" in place of
`selected_session()`) queues the `sensor_select` cursor before the window's own
write/read. `BleSession.request_read()` now rides the same FIFO as writes so
"set cursor, then read that slot" stays ordered. The **Insert Food / Exercise /
PISA Now** dialogs (`instant_event_dialog.py`) got the same Slot picker
(`_InstantDialog` base) and `MainWindow._send_instant()` sends the one-shot to a
single session with a cursor prefix instead of the old broadcast-to-all
(which applied it N× on a multi-sensor board); scenario JSON actions accept an
optional `slot`.

**Patient names.** `models/board_layout.py` gained `slot_of` / `person_for` /
`device_label` / `session_name`: when a slot is assigned a patient in
`data/board_layout.json`, the **Bluetooth device list** shows
`"<patient> — Sensor N"`, the **tree row / graph** show the patient
(`BleSession(display_name=…)` → `_user_id`), and `BluetoothWindow.relabel()`
(called from `MainWindow._on_board_layout_changed`) refreshes the list live.
The advertised name still drives the per-slot demux + pairing decision — only
the label changes. Unassigned slots keep the raw `"Nordic Glucose Sensor N"`.

**Tests.** `e2e_4sensor.py` F12 (config-window target-slot send + read),
F13 (instant-dialog target-slot, no 4× broadcast), F14 (patient-name label
resolution + live tree row).

### 23. MARD + Error Grid (expected vs received)
**Problem.** The thesis is about CGM *sensor accuracy*; the app compares two
lines visually but computes no standard accuracy metric.
**Sketch.** In `models/cgm_metrics.py`: MARD (mean |received − expected| /
expected, %), plus a Clarke or Parkes/Consensus Error Grid — bin each
(expected, received) pair into zones A–E and report the % per zone. A "Accuracy"
tab in the metrics window (#1) with the MARD number and the grid scatter
(matplotlib). Works from a live run or a recording (#3). **Touches.**
`cgm_metrics.py`, `graphic/metrics_window.py`. **Value.** The single most
expected number/figure in a CGM-sensor thesis.

### 24. Sensor-noise-model comparison sweep
**Sketch.** A batch runner (CLI or a "Compare noise models" button) that, for a
fixed glucose profile (a model run or a CSV), streams it through each sensor
noise model (Ideal / Breton / Facchinetti) at high speed and collects
MARD / CV / SD / Error-Grid zone %s into one table + an overlaid plot. Reuse the
scenario runner's dispatch and, on the board side, `e2e_4sensor.py`'s per-slot
plumbing (run all three noise models as slots 1–3 against the same source on
slot 0). **Touches.** new `scripts/noise_sweep.py` or `models/sweep.py`,
`cgm_metrics.py` (#23). **Value.** A ready-made results chapter comparing the
three noise models.

### 25. Deterministic noise RNG seed
**Problem.** `init_slot()` seeds each sensor's RNG from `k_uptime_get_32()`, so
no two runs are bit-identical — bad for reproducible thesis figures and for
diffing a recording (#3) against a re-run.
**Sketch.** A `uint32_t noise_seed` in `struct sensor_slot` (0 = "use uptime",
non-zero = fixed); a "Seed" field in Sensor config / Board Layout. `models/`
Python noise ports get the same knob so the expected-vs-received comparison is
reproducible on both sides. **Touches.** `sim_config.h`, `model_thread.c`
(`init_slot`), `models/sensors.py`, sensor UI. **Effort.** S. **Value.**
Reproducibility; pairs with #3 / #23 / #24.

---

## Suggested near-term order (for the thesis)

1. **#23** MARD + Error Grid — the metric a CGM-sensor thesis is judged on.
2. **#2** model-line alerts (tiny, closes a visible gap) + **#1** clinical
   metrics on both lines + CSV export.
3. **#21** speed as a live scalar — small firmware fix, removes a real gotcha
   and lets **#3** session record/replay capture clean speed changes.
4. **#3** session record/replay → **#4** results report → **#24** noise-model
   comparison sweep (the multi-sensor board makes this almost free).
5. **#20** multi-slot graph overlay — the headline "4 patients, 1 board" figure.
6. **#18** more fault types; **#19** per-slot run/pause/stop if demos need it.

Done this cycle: **#5 multi-sensor Phase 1 (firmware) + Phase 2 (Board Layout
window) + `scripts/e2e_4sensor.py` (11 cases)** — HW-verified; the Windows
multi-identity LE-SC pairing bug was investigated on hardware and worked around
with `CONFIG_APP_CGMS_NO_AUTH`. Earlier cycles: #3 (CSV playback), #16
(partial), #17, #18 (scaffold), PISA (#2/#4 firmware), the x1–x1000 speed
multiplier, the rolling graph window, and `scripts/e2e.py`.
