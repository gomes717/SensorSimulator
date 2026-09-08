# BLE payload validation

Two questions this document answers:

1. **Is the CGM data our board streams the same as what a real Dexcom sensor
   streams over BLE?** — Short answer: **no, and it was never meant to be.**
   Our firmware speaks the *Bluetooth SIG* Continuous Glucose Monitoring
   Service (a public standard); Dexcom's consumer transmitters (G6/G7/ONE+)
   speak a *proprietary* Dexcom protocol. They share the concept ("SFLOAT
   glucose in mg/dL over GATT notifications") but not the framing.
2. **Can we independently check that the values on the wire are correct?** —
   Yes. We have the Python model ports (`src/models/`) and, for CSV-backed
   sensors, the source recording itself, so the *expected* stream is
   computable and can be diffed against the *received* stream. See
   [§3 Cross-validation](#3-cross-validation-model--csv-vs-received).

---

## 1. What our board actually puts on the wire

The firmware (`firmware/peripheral_cgms` + the patched CGMS service under
`firmware/overlay/nrf/subsys/bluetooth/services/cgms`) advertises and serves the
standard **Continuous Glucose Monitoring Service**, UUID `0x181F`
(`0000181f-0000-1000-8000-00805f9b34fb`).

### 1.1 CGM Measurement notification — `0x2AA7`

Built in `cgms.c`'s `bt_cgms_notify_meas()`. Our build always emits the
**minimal 6-byte record** (no optional fields):

| Offset | Size | Field | Value in our build |
|---|---|---|---|
| 0 | `uint8` | **Size** | `0x06` (total record length) |
| 1 | `uint8` | **Flags** | `0x00` — no trend, no quality, no sensor-status annunciation |
| 2 | `SFLOAT` (LE `uint16`) | **CGM Glucose Concentration** (mg/dL) | `sfloat_from_float(glucose_mg_dl)` |
| 4 | `uint16` (LE) | **Time Offset** (minutes since Session Start) | `(uptime_ms - local_start_time) / 60000` |

`meas.flag = 0` and all three `sensor_status_annunciation` bytes are `0`
(`bt_cgms_measurement_add()` in `cgms.c`), so the optional Sensor Status
Annunciation / Trend / Quality / E2E-CRC fields are **never present**.

`SFLOAT` (IEEE-11073 16-bit): `[4-bit signed exponent | 12-bit signed mantissa]`,
value = `mantissa × 10^exponent`. Decoded app-side by
`services/ble_session.py:_sfloat_to_float()` / `_decode_cgm_measurement()`.

Example: glucose `123 mg/dL`, ~2 min into the session →
`06 00 7B 00 02 00` (mantissa 123, exp 0 → `0x007B`; time offset `0x0002`).

**In CSV-playback mode the framing is byte-for-byte identical** — only the
*source* of the glucose number changes (a recorded sample instead of a model
output). See [PROTOCOL_SPEC.md](../PROTOCOL_SPEC.md) "CSV playback data source".

### 1.2 Other CGMS characteristics we expose

| Characteristic | UUID | Notes on our build |
|---|---|---|
| CGM Feature | `0x2AA8` | `le24` feature bitmask + Type/Sample-Location byte + `0xFFFF` E2E-CRC placeholder. Type = capillary plasma, location = finger. |
| CGM Status | `0x2AA9` | `uint16` time offset + status/cal-temp/warning bytes (all `0` normally). |
| CGM Session Start Time | `0x2AAA` | year(`le16`)/month/day/hour/min/sec/timezone/DST — client-writable. |
| CGM Session Run Time | `0x2AAB` | fixed `1` (hour). |
| Record Access Control Point (RACP) | `0x2AAC` | write + indicate; historical record report/delete/count. |
| CGM Specific Ops Control Point (SOCP) | `0x2AAD` | write + indicate; **patched** to take the comm interval in *seconds* (spec says minutes). |

All require an authenticated (paired + encrypted) link
(`BT_GATT_PERM_*_AUTHEN`). Pairing is passkey-based, fixed test passkey
`123456`.

### 1.3 Deviations from the letter of the SIG spec

- **SOCP communication interval in seconds, not minutes** (`report_meas()` uses
  `K_SECONDS(cgms->comm_interval)`). Deliberate, for bench testing.
- **No E2E-CRC** even though CGM Feature reserves the field — our Measurement
  Flags never set the "device supports E2E" path.
- **Periodic re-notification of the last record** by the library's own timer
  (`report_meas()`), independent of when a fresh value is produced.

Everything else (service UUID, characteristic UUIDs, Measurement field order,
SFLOAT encoding, Time Offset semantics) matches the SIG **CGMS v1.0.2**
specification.

---

## 2. What a real Dexcom sensor puts on the wire

Dexcom's consumer CGMs (G6, G7, ONE, ONE+) do **not** implement the SIG CGM
Service. From public reverse-engineering (xDrip+, DiaBLE, Loop/LoopKit
community) the transmitter/sensor exposes a **vendor-specific** GATT service:

| Item | Real Dexcom (G6 / G7) | Ours |
|---|---|---|
| Advertised name | `DXCM..` / `DX02..` (last chars = pairing code) | `Nordic Glucose Sensor` |
| Primary service | proprietary `FEBC` short UUID; full 128-bit base `F8083532-849E-531C-C594-30F1F86A4EA5` | SIG `0x181F` |
| Key characteristics | `…3535` **Control**, `…3536` legacy Auth, `…3538` **Backfill** (G7), `…3534`/`…3537` others — all under the F8083 base | SIG `0x2AA7` Measurement, `0x2AA8` Feature, `0x2AAC` RACP, `0x2AAD` SOCP |
| Session security | **J-PAKE** challenge/response (G7, mbedTLS-based) or AES challenge (G6) over the Control characteristic before any glucose flows | Standard BLE SMP pairing (LE Secure Connections), fixed passkey |
| Glucose framing | Dexcom **opcode-tagged binary messages** on Control/Backfill, e.g. a "glucose" message: `opcode(1) | status(1) | sequence(4) | timestamp(4) | glucose(2, little-endian uint, low 12 bits) | state(1) | trend(1) | …`; backfill request opcode `0x59` with an 8-byte start/end-time payload streaming ~24 h of history | SIG 6-byte record: `size | flags | SFLOAT glucose | u16 time-offset` |
| Glucose value type | plain little-endian integer mg/dL (mantissa in low 12 bits, top bits are display flags) | IEEE-11073 **SFLOAT** (exponent + mantissa) |
| Time reference | absolute transmitter epoch seconds in every message | minutes since a client-set Session Start Time |
| Realtime cadence | one message every 300 s (sensor-driven) | every `comm_interval` seconds (5 s here), library-driven |
| Encryption of glucose | realtime + backfill values are **not** encrypted after auth | link-layer encrypted (BLE SMP) |

**Conclusion:** our payload is a faithful **Bluetooth SIG CGM Measurement**;
Dexcom's is a different, proprietary encoding. A generic SIG-CGMS client would
read our board fine and would *not* read a Dexcom sensor without implementing
the Dexcom protocol. The project's own app (`services/ble_session.py`) is a
SIG-CGMS client — it only decodes `0x2AA7`.

If byte-compatibility with a real Dexcom transmitter is ever a requirement,
that is a separate firmware effort (new vendor service + J-PAKE + opcode
messages) and should be tracked as its own item in `docs/TODO.md`.

### 2.1 The dataset CSVs are *not* a BLE capture

`dataset/Dexcom_00X.csv` are **Dexcom Clarity account exports** (5-min EGV rows,
`Glucose Value (mg/dL)` column) — a post-processed report, not the BLE wire
data. `dataset/Food_Log_00X.csv` are meal diaries (`time_begin`, `total_carb`).
They are the *source of truth for the numbers we replay*, not for the transport.

---

## 3. Cross-validation: model / CSV vs. received

Because both sides of the value pipeline are reproducible in Python, we can
verify the board is streaming the right numbers, not just well-formed records.

### 3.1 Model-backed sensor

`src/models/{cambridge,uva_padova,royparker,deichmann}.py` are numerically
identical ports of the firmware's `cgmsim_*.c`. `src/models/engine.py` steps
them with the *same* `dt_min` rule the firmware uses. With the **Ideal** sensor
(no noise) the expected CGM value at simulated minute *t* is
`engine.expected_reading` at *t*; with Breton/Facchinetti the on-device noise is
stochastic, so compare **distribution / moving-average**, not sample-by-sample.

Expected vs received should agree to within:

- Ideal sensor: `|Δ| ≤ 0.5 mg/dL` (SFLOAT rounding + float32 vs float64).
- Noisy sensor: matching mean ± a few mg/dL over a ≥30-sample window,
  matching trend direction.

### 3.2 CSV-backed sensor

Exact check. The firmware maps `row = floor(sim_clock_min·60 / interval_s)` then
`row %= row_count` and emits that `int16` verbatim (no noise —
`model_thread.c`'s CSV branch). So for a received notification at session time
offset `T` minutes:

```
expected_mg_dl = glucose_track[ floor(T·60 / interval_s) % row_count ]
```

where `glucose_track` is what `api.protocol.build_glucose_track()` packed from
`models.dexcom_csv.slice_window(...) + resample(...)`. Tolerance: `|Δ| ≤ 1`
mg/dL (int16 vs the SFLOAT round-trip).

Food Log: each `{offset_s, carbs_g}` entry should surface in a Food/Exercise
Status notification (`carbs_g_per_min > 0`) within `CSV_FOODLOG_SPREAD_MIN`
(30 min) after `offset_s`, report-only — it must **not** move the glucose
stream.

### 3.3 The checker script

`scripts/validate_ble_stream.py` (stdlib + `bleak`) connects to the board,
subscribes to `0x2AA7` (and the Food/Exercise Status characteristic), rebuilds
the expected series from a `PersonProfile` (model) or a CSV window, and prints a
per-sample diff table plus pass/fail against the tolerances above. Run it
alongside a normal app session or standalone. See the script's `--help`.

### 3.4 UI-level end-to-end

`scripts/ui_smoke.py` drives the real PyQt app (synthetic `QTest` clicks,
monkeypatched modal dialogs, `widget.grab()` screenshots) through four
scenarios and asserts on the live graph/metrics state:

| Scenario | Path exercised |
|---|---|
| **A** CSV on board | connect board → CSV Analysis (open + slider + assign) → Send CSV to Board → Fast mode → Start → received line carries the CSV rows (not the flat model line), stats populate |
| **B** model on board | model patient → Fast → Start → board streams + local "expected" model line runs in parallel |
| **C** model + food | Model Only, Cambridge → x60 → Start → Insert Food Now → carbs trapezoid on the food/exercise graph, glucose excursion, stats update |
| **D** model + exercise | Model Only, Deichmann → x60 → Start → Insert Exercise Now → exercise step on the graph, glucose drop |
| **E** model + PISA | Model Only, Cambridge → x60 → Start → Insert PISA Now → glucose dips to ≈ level·(1−depth) at the midpoint and recovers; the interval is shaded (`_pisa_spans` / `_pisa_patches`) |
| **F** rolling window | run, shrink the View-window to a few seconds → only the recent slice is shown (`_visible_xlim` + windowed metrics) → "Entire run" restores the full span |

All pass; screenshots land in `scratchpad_ui/` (gitignored). The speed
multiplier is set via the Configuration-window slider (`_speed_to_slider`).

### 3.5 Hardware result (2026-09)

CSV playback verified end-to-end on the nRF54L15 DK: upload (BEGIN/DATA/COMMIT +
CRC-32) of a 20-row `int16` track, `data_source` → CSV, then the CGM
Measurement stream carried **exactly** those rows, advancing as
`floor(sim_clock_min·60 / interval_s) % row_count` and **looping** cleanly at
the window end, with no model value bleeding through. The manifest survived a
power-cycle (autonomous resume). One firmware bug was found and fixed in the
process — a queued config write (`data_source`) was dropped when the app's
Start sequence sent run-state STOPPED immediately after it; see
[PROTOCOL_SPEC.md](../PROTOCOL_SPEC.md) "Run state".

---

## Sources

- [Bluetooth SIG — Continuous Glucose Monitoring Service v1.0.2](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/CGMS_v1.0.2/out/en/index-en.html)
- [Bluetooth SIG — Continuous Glucose Monitoring Profile v1.0.2](https://www.bluetooth.com/specifications/specs/cgmp-1-0-2/)
- [Nordic — peripheral_cgms sample / CGMS service docs](https://docs.nordicsemi.com/bundle/ncs-2.9.0/page/nrf/samples/bluetooth/peripheral_cgms/README.html)
- [DiaBLE — "G7 without Dexcom app / Authentication" (protocol notes)](https://github.com/gui-dos/DiaBLE/discussions/17)
- [NightscoutFoundation/xDrip — Dexcom G7 backfill discussions](https://github.com/NightscoutFoundation/xDrip/issues/4442)
- [xDrip & Dexcom documentation](https://navid200.github.io/xDrip/docs/Dexcom_page.html)
