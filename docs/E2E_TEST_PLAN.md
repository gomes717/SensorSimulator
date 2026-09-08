# End-to-end test plan — app ↔ microcontroller

Full-stack verification of the SensorSimulator app driving the nRF54L15 DK
firmware over BLE. Every run captures three synchronized log streams and, on any
failure, freezes them into a per-case artifact folder so a failure can be
diagnosed after the fact without re-running.

- **System under test**: `src/` (PyQt6 app) + `firmware/peripheral_cgms/`
  (Zephyr firmware) + the real BLE link between them.
- **Contract under test**: [`PROTOCOL_SPEC.md`](../PROTOCOL_SPEC.md) §2–§8,
  [`docs/FIRMWARE.md`](FIRMWARE.md), [`docs/BLE_PAYLOAD_VALIDATION.md`](BLE_PAYLOAD_VALIDATION.md).
- **Related, already-built tooling this plan extends**:
  `scripts/ui_smoke.py` (UI scenarios A–F), `scripts/validate_ble_stream.py`
  (CSV/model stream diff), `firmware/scripts/{build,flash}.ps1`.

---

## 1. Objectives

1. Prove each BLE characteristic and firmware behavior works against the real
   board, not just in isolation.
2. Prove the app's live graph / metrics / expected-line reflect what the board
   actually streams.
3. Make every failure **self-documenting**: the exact firmware console output,
   BLE traffic, app log, and a screenshot at the moment of failure are stored.
4. Be re-runnable on demand (before a commit, after any firmware change) in
   ~10 min for the smoke subset, ~30–40 min for the full matrix.

Out of scope: multi-sensor / multi-identity (deferred, see `docs/TODO.md`),
automated CI on hardware (no bench board in CI), Dexcom-proprietary BLE.

---

## 2. Test environment

| Item | Value / how |
|---|---|
| Board | nRF54L15 DK, `nrf54l15dk/nrf54l15/cpuapp`, HCI name `nRF54L15_M33` |
| Firmware | flashed from **this repo's** `firmware/` via `firmware\scripts\build.ps1` + `flash.ps1` — record the git SHA in `environment.json` |
| BLE address | default `D0:3F:4D:E2:7C:9B` (override `--board`) |
| Serial console | **COM10** @ 115200 8N1 (COM9 silent) — `System.IO.Ports.SerialPort` from PowerShell only |
| Pairing | fixed passkey `123456`; `services/windows_ble_pairing.py` re-pairs each connect |
| Host | Windows 11, project `.venv` (PyQt6 + bleak + winrt) |
| J-Link | `C:\Program Files\SEGGER\JLink_V924a\JLink.exe` for reset-to-catch-boot |

**Preconditions checked by the harness before any case runs** (fail fast, no
artifacts): COM10 opens and emits `model_tick:` lines; `bleak` can discover the
board; `.venv` imports `src.api.protocol`; firmware git SHA is committed or the
run is tagged `dirty`.

---

## 3. Harness architecture — `scripts/e2e.py`

**Status (2026-09): implemented.** `scripts/e2e.py` is the runner described
below. It currently ships a *representative subset* of the §4 matrix — the case
IDs marked ✅ in §4 — plus `--loop N` (run the whole thing N times back to
back). Adding a case is a `with Case(...) as c:` function registered in
`SUITES`. Latest hardware run: **11/11 PASS** (S1-02, S2-01, S3-03/04, S4-05,
S5-02, S7-03, S8-02, S10-01, S16-01, S17-01).

Extends the `ui_smoke.py` driving style (`QTest` synthetic input, monkeypatched
modal dialogs, `widget.grab()` screenshots) with a **capture + artifact layer**.

### 3.1 Three capture streams, one clock

| Stream | Source | Sink |
|---|---|---|
| `serial` | background thread reading COM10 | `serial.log` (whole run) + a 4000-line `deque` |
| `app` | `sys.stdout`/`sys.stderr` tee'd at process start (the app uses `print()` throughout) | `app.log` + 2000-line `deque` |
| `ble` | slot on `MainWindow._ble_log.new_message` + a wrapper around `BleSession.queue_write` / `start_csv_upload` | `ble.jsonl` (one JSON object per line: dir, char, hex, decoded, t) + 1000-entry `deque` |

All three are timestamped with `time.monotonic()` relative to run start; each
`Case` writes a marker line (`>>> CASE S5-02 speed_x1000`) into all three so
slices can be cut per case.

### 3.2 Artifact layout (per run)

```
test-artifacts/<run-id>/            run-id = UTC yyyymmdd-HHMMSSZ
  SUMMARY.md                        counts + one line per case (verdict, duration, note)
  environment.json                  board addr, fw SHA (+dirty), app SHA, python/pkg versions,
                                    COM port, host, start/end time
  serial.log        app.log         full streams for the whole run
  ble.jsonl                         every BLE message + every write the harness issued
  run.jsonl                         harness events: case_start, step, assert(name,ok,detail),
                                    case_end(verdict), preflight, teardown
  cases/<CASE-ID>_<slug>/
    result.json                     verdict, t_start/t_end, measured vs expected values
    serial.slice.log                serial lines between this case's start/end markers
    screenshot.png                  main window at case end (or at failure)
    FAILURE.md                      ── written ONLY on FAIL/ERROR ──
```

### 3.3 `FAILURE.md` contents (the "stores when error occurs" requirement)

Written by the `Case` context manager when it catches `AssertionError`,
any `Exception`, or a step timeout:

```
# S5-02 speed_x1000 — FAIL

Failed assertion: board dt after x1000 write
  expected: dt in [16.0, 17.0]  (dt_min = 1/60 * 1000)
  actual:   dt = 1.0000  (board still at x60)
  at: scripts/e2e.py:themethod  line 214

## Timeline (relative s)
  0.00  case_start
  0.12  write speed=1000.0  -> 5b2c0012  hex=0000807a
  0.61  ble: reset_sync gen=7
  2.10  assert FAILED

## Firmware serial — last 120 lines
<tail of serial.slice.log>

## BLE traffic — last 60 messages
<tail of this case's ble.jsonl slice>

## App log — last 40 lines
<tail of app deque>

## Python traceback (if exception)
<traceback>
```

`ble.jsonl` and `serial.log` for the **whole run** are always kept (pass or
fail) so a flaky pass can still be inspected. `FAILURE.md` + `screenshot.png`
are the fast path.

### 3.4 Failure taxonomy recorded in `result.json`

| `verdict` | meaning |
|---|---|
| `PASS` | all assertions held |
| `FAIL` | an assertion did not hold (behavior wrong) |
| `ERROR` | unexpected exception / disconnect / GATT error |
| `TIMEOUT` | a `wait_until` deadline expired |
| `SKIP` | precondition not met (e.g. `--no-board`, model not applicable) |

Exit code: `0` iff no `FAIL`/`ERROR`/`TIMEOUT`. `SUMMARY.md` + stdout print the
counts and the artifact path.

### 3.5 Invocation

```powershell
python scripts\e2e.py                       # full matrix, default board
python scripts\e2e.py --smoke               # S1,S2,S5,S6a,S7a,S8a only (~10 min)
python scripts\e2e.py --only S8,S12          # selected suites
python scripts\e2e.py --board AA:BB:.. --no-board   # skip board suites
python scripts\e2e.py --reflash             # build.ps1 -Pristine + flash.ps1 first
```

`--reflash` records the fresh SHA; otherwise the harness reads
`firmware/build/.../zephyr.dts`-adjacent metadata and warns if the tree is dirty
vs the flashed image is unknown.

---

## 4. Test matrix

Case IDs are stable (`S<suite>-<nn>`). Each case: **Pre** (setup) → **Do**
(steps) → **Expect** (assertions) → **Capture on fail** (beyond the always-on
streams). "Board" cases `SKIP` under `--no-board`.

### S1 — Connectivity & pairing

| ID | Do | Expect |
|---|---|---|
| S1-01 | scan for the board name | discovered within 15 s |
| S1-02 | `BleSession` connect + pair (fixed passkey) | `connected` signal, `subscribed > 0`, no `auth` error in `last_error` |
| S1-03 | subscribe to every notify/indicate char | subscribe count == notify count; CSV control + reset_sync + food/ex status present |
| S1-04 | disconnect, then reconnect | second connect succeeds; CGM stream resumes |
| S1-05 | drop the link mid-session (stop session), board keeps advertising | re-scan finds it; reconnect OK (`main.c` `disconnected()` restarts adv) |
| S1-06 | MTU: issue a 137-byte person-config write right after connect | write succeeds (no "Prepare Queue Full"); serial shows `cfg write received type=0 len=137` |

### S2 — Standard CGMS stream (`0x181F` / `0x2AA7`)

| ID | Do | Expect |
|---|---|---|
| S2-01 | subscribe `0x2AA7`, collect 60 s at x1 | ≥ 8 notifications; each 6 bytes `06 00 <sflo> <sflo> <to> <to>` |
| S2-02 | decode SFLOAT vs `_sfloat_to_float` | every value 20–500 mg/dL, matches `model_tick` `reading=` on serial ±0.5 |
| S2-03 | time-offset field increments with wall time | `time_offset_min` monotonic, ~1/min at x1 |
| S2-04 | read `0x2AA8` CGM Feature, `0x2AA9` Status, `0x2AAB` Run Time | Feature type = capillary-plasma/finger; Run Time = 1 |
| S2-05 | SOCP write comm-interval = 5 s | subsequent notify spacing ≈ 5 s (patched: seconds not minutes) |

### S3 — Custom config service round-trips (`5b2c0002`…`5b2c0011`)

For each writable+readable char: write a known value → wait `reset_sync` →
read back → assert equal (byte-exact through `protocol.decode_*`).

| ID | Char | Value written |
|---|---|---|
| S3-01 | person `5b2c0002` | UVA/Padova with non-default `BW`, `VG` |
| S3-02 | sensor `5b2c0003` | Facchinetti with non-default `sigma_v` |
| S3-03 | speed `5b2c0012` | x37.5 (verify float round-trip) |
| S3-04 | data source `5b2c0011` | 1 (csv) then 0 (model) |
| S3-05 | food events `5b2c0005`+readback `5b2c0008` | clear, then 3 events; readback count == 3, fields match |
| S3-06 | exercise events `5b2c0006`+`5b2c0009` | clear, then 2 events |
| S3-07 | every config write triggers `reset_sync` and a `model_thread: applied config` line | serial confirms; `sim_clock_min` resets to 0 |
| S3-08 | persistence: after S3-01/02, power-cycle (J-Link reset) | boot log `sim_config: loaded from flash` with the written model/sensor ids |

### S4 — Run-state lifecycle (`5b2c000a`)

| ID | Do | Expect |
|---|---|---|
| S4-01 | write `1` running | `model_tick` ticking; CGM stream live |
| S4-02 | write `2` paused | `model_thread: idle (run_state=2)`; `sim_clock_min` frozen; last CGM value repeats (library re-notify) |
| S4-03 | write `1` resume | ticks continue from the frozen clock (no reset) |
| S4-04 | write `0` stopped | `sim_clock_min` back to 0, model re-init, `reset_sync` fires, then held |
| S4-05 | `data_source` write **then** `0` **then** `1` back-to-back (the app's Start sequence) | the pending `data_source` is applied, not dropped (regression: the 2026-09 `!cfg_pending` fix) |
| S4-06 | app Start/Pause/Stop buttons drive both sides | app `_run_state` and board agree; graph clears on Stop |

### S5 — Speed multiplier (`5b2c0012`)

| ID | Do | Expect |
|---|---|---|
| S5-01 | write x1 | serial `dt=0.0167`; ~1 sim-min/min |
| S5-02 | write x60 | serial `dt=1.0000`; CSV rows / model steps advance 60× |
| S5-03 | write x1000 | serial `dt≈16.667`; readback == 1000.0 |
| S5-04 | write x0.2 and x5000 | clamped to 1.0 / 1000.0 on readback |
| S5-05 | slider in Configuration → `_on_speed_changed` → board + engine | engine `[engine] dt=` matches board `dt=` within one step |
| S5-06 | change speed mid-run | no reset of `sim_clock_min` beyond the normal config-write reset; stream stays continuous otherwise |

### S6 — Physiological models (per model: Cambridge, UVA/Padova, Roy&Parker, Deichmann)

| ID | Do | Expect |
|---|---|---|
| S6-a1..d1 | apply model defaults, x60, run 10 min sim | steady state within ±3 mg/dL of `Gpeq`/model equilibrium |
| S6-a2..d2 | recurring food event (60 g/15 min) at t+2 min | glucose rises; peak in a physiologically plausible window; returns toward baseline |
| S6-c3/d3 | recurring exercise (30 min, 70 %) — Roy&Parker & Deichmann only | glucose drops during the bout, recovers after |
| S6-a4..d4 | expected (Python) vs received (board, Ideal sensor) | `|Δ| ≤ 0.5 mg/dL` sample-wise (`validate_ble_stream.py --model` style) |
| S6-a5..d5 | Breton / Facchinetti noise sensor | received mean tracks expected ±3 mg/dL over a ≥ 30-sample window; not frozen |

### S7 — Instant events (non-persisted, non-reset)

| ID | Do | Expect |
|---|---|---|
| S7-01 | Insert Food Now 80 g / 10 min (model) | serial `instant food added`; carbs on Food/Exercise Status; glucose excursion; `sim_clock_min` **not** reset |
| S7-02 | Insert Exercise Now 30 min / 70 % (Deichmann) | serial `instant exercise added`; exercise_pct on status; glucose drop |
| S7-03 | Insert PISA Now 40 % / 10 min | serial `instant PISA added` + `pisa` factor tracing `→0.60→` and back; **streamed** value dips to ≈ level·0.6 while **model `glucose=` unchanged**; app shades the interval (`_pisa_spans` len == 1) |
| S7-04 | PISA on the CSV data source | CSV rows still emitted but attenuated by the same factor |
| S7-05 | fire a config write (e.g. Stop) while an instant event is active | instant slots cleared by `apply_config_locked()` (no bleed into next run) |
| S7-06 | > 8 instant events of one kind queued | 9th logged as "dropped, no free slot", not a crash |

### S8 — CSV data source (`5b2c000f` / `5b2c0010` / `5b2c0011`)

| ID | Do | Expect |
|---|---|---|
| S8-01 | build a 288-row window from `dataset/Dexcom_001.csv`; BEGIN → DATA chunks → COMMIT | control notify `status=OK` after BEGIN and after COMMIT; serial `csv_store: commit ok track=0` |
| S8-02 | set `data_source=1`, run x60 | streamed values are exactly the resampled rows (`|Δ| ≤ 1 mg/dL`), **not** the model's flat line |
| S8-03 | run past the window end | playback **loops** (`row %= row_count`) |
| S8-04 | upload the matching food log track (auto-paired by ID) | meals surface on Food/Exercise Status ≤ 30 sim-min after their offset; glucose **not** altered by them |
| S8-05 | power-cycle after COMMIT | boot log `csv_store: manifest loaded (glucose present=1 …)`; playback auto-resumes if `data_source` was 1 |
| S8-06 | bad CRC in BEGIN header | COMMIT notify `status=ERR`; serial `commit CRC mismatch`; previous manifest untouched |
| S8-07 | `total_bytes` > region size | BEGIN notify `status=ERR` (`-EFBIG`) |
| S8-08 | disconnect mid-upload, reconnect, ABORT, re-upload | clean recovery; no partial track committed |
| S8-09 | app path: CSV Analysis "Assign window to person…" → Configuration "Send CSV to Board" | `csv_upload_finished(ok=True)`; board switches to CSV; expected line == received line |

### S9 — CGMS Only mode (`5b2c000e`)

| ID | Do | Expect |
|---|---|---|
| S9-01 | enable (write 1) | simulation keeps running (no reset); only `0x2AA7` notifications; Food/Exercise Status count drops to 0 |
| S9-02 | attempt any config write while enabled | GATT error `Write Not Permitted` (`0x03`); serial shows the reject |
| S9-03 | run-state and cgms-only chars still writable | can still Start/Stop and toggle back |
| S9-04 | disable (write 0) | full reset + hold; requires explicit `RUNNING` to resume |
| S9-05 | app checkbox locks the right controls | Person/Sensor/Food/Exercise/Speed/Start/Insert-* disabled; local engine discarded |

### S10 — Graph, metrics, alerts (app-side, board feeding it)

| ID | Do | Expect |
|---|---|---|
| S10-01 | rolling window = "Last 1 hour"; run long enough | `_visible_xlim` width ≤ 3600 s; "Entire run" restores full span; data arrays never trimmed |
| S10-02 | range metrics track the **visible** window | TIR/TBR/TAR/mean/variance recomputed from `_in_view(...)` |
| S10-03 | drive glucose < `tbr1_below` and > `tar1_above` | tree row shows `▼ LOW` / `▲ HIGH` badge (BLE path); received line recolors red/yellow/green by `_category` |
| S10-04 | PISA-driven false low crosses `tbr1` | LOW badge appears even though the model is euglycemic (documents current behavior — see `docs/TODO.md` feature idea "model-line alerts") |
| S10-05 | theme switch mid-run | graphs rebuild, PISA spans + range bands + data survive |

### S11 — Persistence & autonomy

| ID | Do | Expect |
|---|---|---|
| S11-01 | configure model+sensor+speed+events+CSV, disconnect, power-cycle | board comes up streaming with the last-saved config, no app connected |
| S11-02 | corrupt the sim_config partition (write garbage via a debug path or just flash a version bump) | boot falls back to `sim_config_set_defaults()`; still boots and streams |
| S11-03 | firmware version mismatch (v2 image over v3 flash) | defaults used, logged, no hang |

### S12 — Robustness / negative

| ID | Do | Expect |
|---|---|---|
| S12-01 | malformed writes (short person config, odd-length speed, empty CSV control) | GATT `Invalid Attribute Length` / handler `return`, no crash |
| S12-02 | flood the config queue (16+ rapid writes) | some return `Insufficient Resources`; board recovers; no deadlock |
| S12-03 | CSV DATA write with non-monotonic / out-of-range offset | rejected (`-EINVAL`), COMMIT later fails cleanly |
| S12-04 | subscribe/unsubscribe `0x2AA7` repeatedly | no leak, notifications resume each time |
| S12-05 | 30-minute soak at x1000 | no watchdog reset, RAM stable, `sim_clock_min` keeps advancing, stream never freezes |
| S12-06 | pull board power during a flash write (`sim_config_save_to_flash`) | next boot either loads the old or defaults — never a hang (documents flash-write atomicity risk) |

### S16 — Comm profile (`5b2c0014` — SIG CGMS vs Dexcom-style)

| ID | Do | Expect |
|---|---|---|
| ✅ S16-01 | force SIG → switch to Dexcom → switch back to SIG, reconnecting each time | SIG stream (`0x2AA7`, has `flags`) then Dexcom stream (`FEBC` `…3538`, 14-byte msg, has `sequence`) then SIG again; all decode to plausible mg/dL; serial `pushed dexcom` while active; `Advertising started (comm_profile=dexcom)` on the reconnect |
| S16-02 | write comm_profile while connected | switch **deferred**: serial `re-advertise deferred until disconnect`, applied on the next `disconnected()` |
| S16-03 | persistence | power-cycle after Dexcom write → boots advertising `DXCM01` |
| S16-04 | PISA + speed in Dexcom mode | both still shape the streamed value (they act upstream in `model_thread`) |

### S17 — Scenario runner (`models/scenario.py`, `scenarios/*.json`)

| ID | Do | Expect |
|---|---|---|
| ✅ S17-01 | load `scenarios/demo_pisa.json`, run it (timeline compressed 10× for the test) | every action dispatches (`speed`, `insert_food`, `inject_fault`/PISA, `run_state` start+stop); `_speed_mult` reaches x60; the PISA action shades the graph mid-run |
| S17-02 | each shipped scenario loads and its actions all map to a known `kind` | no `(unknown action …)` in the step log |

---

## 5. Per-case authoring template

```python
with Case("S7-03", "pisa_false_low", suite="S7") as c:
    c.step("apply Cambridge + Ideal, x60, running")
    ...
    base = c.measure("baseline", median(last_n(stream, 3)))
    c.step("write PISA 40% / 10 min")
    sess.queue_write("pisa_instant", protocol.encode_pisa_instant(10, 0.40))
    c.wait_until(lambda: len(stream) >= base_n + 12, 25, "post-event samples")
    trough = min(stream[base_n:])
    c.assert_(trough < base * 0.75, "streamed value dips >=25%",
              detail=f"base={base:.1f} trough={trough:.1f}")
    c.assert_("pisa=0.6" in serial.tail(200) or serial_min_pisa() <= 0.62,
              "firmware pisa factor reached ~0.60")
    c.assert_(model_glucose_flat(serial), "underlying model glucose unchanged")
    c.assert_(len(w._pisa_spans) == 1, "graph interval shaded")
```

`Case.assert_` records `assert(name, ok, detail)` to `run.jsonl`; the first
false one raises, the context manager writes `FAILURE.md` with the stream tails
+ traceback + screenshot and sets `verdict=FAIL`.

---

## 6. Execution cadence

| When | What |
|---|---|
| Before every commit that touches `src/` or `firmware/` | `python scripts\e2e.py --smoke` |
| After any `firmware/` change | `python scripts\e2e.py --reflash --only S3,S4,S5,S7,S8,S11` |
| Before tagging a thesis milestone | full `python scripts\e2e.py` + archive `test-artifacts/<run-id>/` |
| Investigating a field issue | re-run the single failing `--only S8-06` and read its `FAILURE.md` |

Keep the last ~10 run folders; older ones are safe to delete (add
`test-artifacts/` to `.gitignore`).

---

## 7. Traceability

| Suite | Spec / doc | TODO item |
|---|---|---|
| S2 | PROTOCOL_SPEC §1, BLE_PAYLOAD_VALIDATION §1 | — |
| S3, S4 | PROTOCOL_SPEC §2 (person/sensor/mode/events/run-state), §5 | — |
| S5 | PROTOCOL_SPEC "Speed" §2, FIRMWARE §2 | #5 (done) |
| S6 | MODELS.md, PROTOCOL_SPEC §4 | #1 (metrics) |
| S7 | PROTOCOL_SPEC "Instant … event" §2 | #2/#4 (PISA, done) |
| S8 | PROTOCOL_SPEC "CSV playback data source" §2, FIRMWARE §6, BLE_PAYLOAD_VALIDATION §3 | #3 (done) |
| S9 | PROTOCOL_SPEC "CGMS Only mode" §2 | — |
| S10 | main_window graph/metrics; FEATURE_IDEAS "model-line alerts" | #1 |
| S11 | FIRMWARE §3 | — |

---

## 8. Known gaps

- No hardware in CI — this plan is a **local** gate, run by hand.
- Multi-sensor / multi-identity untested (deferred).
- S11-02 / S12-06 need a deliberate corruption/power-cut path; treat as manual
  until a debug hook exists.
- Timing assertions (S2-03, S5, S8-02) allow ±1 sample of slack for the
  ~500 ms comm-thread poll + up-to-1 s model tick.

## 9. Appendix — 5-minute manual smoke (no harness)

1. `firmware\scripts\flash.ps1`; open COM10, see `model_tick:` at `dt=0.0167`.
2. `python src\main.py` → Connect Bluetooth → connect the board → select its tree row.
3. Configuration: Speed slider → x60; serial `dt` jumps to `1.0000`.
4. Start → received (solid) + expected (dashed) lines track; range metrics populate.
5. Insert PISA Now 40 % / 10 min → received line dips ~40 % and recovers; interval shaded.
6. CSV Analysis → open `dataset/Dexcom_001.csv` → Assign to a patient → Configuration → Send CSV to Board → received line becomes the recorded trace.
7. View → Graph time window → Last 1 hour vs Entire run.
8. Power-cycle the board (no app) → it keeps streaming the last config.
