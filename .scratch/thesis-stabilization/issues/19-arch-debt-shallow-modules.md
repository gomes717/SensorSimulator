# Architectural debt: shallow modules + split responsibilities

Status: (see below)
Track: B
Phase: 3 attempt
Blocked by: —

## 2026-09-08 status

**Partial (2026-09-08, commit 05ae500).** Done: BleSession uses `board_layout.slot_of()` (one canonical name→slot, drops `import re`); `protocol.build_csv_uploads()` replaces the duplicated glucose+foodlog upload-list assembly in Configuration + BoardLayoutWindow. Tests added. Not done: the rest of the shallow-module list (ble_message_log pass-through, restart_board wrapper, _ModelAdapter step wrappers, scenario.py, app_settings idioms) — cosmetic, deferred.

---

Not scheduled for the defense. Source: architecture review, 2026-09-08.

## Shallow modules (interface ≈ implementation)

- **`src/core/ble_message_log.py` (28 lines)** — a `list` + one `pyqtSignal`;
  `add_message` appends + emits, `get_messages` returns a copy. Pure pass-through
  between `BleSession` and the windows. Deletion test: complexity vanishes.
- **`graphic/device_target.py::restart_board`** — one `queue_write` statement;
  docstring 8× the body. Callers must remember to call it after config writes.
- **`_cambridge_step` / `_uva_padova_step` / `_royparker_step` /
  `_deichmann_step`** (`engine.py:74-87`) — one-line arg-shuffling wrappers whose
  only job is to give `_ModelAdapter.step` a uniform signature. The adapter
  (`engine.py:90-136`) is 6 callables, half supplied as inline lambdas.
- **`models/scenario.py::ScenarioRunner`** — thin QTimer fan-out; the behaviour
  is entirely in the injected `dispatch` = `MainWindow._scenario_dispatch`
  (`main_window.py:776`), a 65-line `if kind == …` ladder with no dispatch table
  that partly re-implements `_open_insert_*`.
- **`models/board_layout.py`** — `slot_of` / `person_for` / `device_label` /
  `session_name` are four tiny functions over the same trailing-number regex,
  duplicated in `ble_session.py:171`.
- **`models/app_settings.py`** — one JSON file, three serialization idioms:
  `load()/save()` for 4 threshold keys, generic `load_pref/save_pref`,
  `load_theme/save_theme`. Callers must know which pair applies to which key.
- **`models/sensors.py` (25 lines)** — three functions each returning a dict
  literal; default-data only (the noise math is firmware-side).

## Locality smells

- **`engine.load_csv_window` is mislocated** — a pure profile→samples resolver
  parked in `engine.py:32` but imported across from `configuration_window.py:36`,
  `board_layout_window.py:35`, and indirectly `person_config_window`. Belongs in
  a CSV module, not the engine.
- **`board_layout` links slots to profiles by string name** —
  `SlotAssignment.person: str` (`board_layout.py:28-33`), re-resolved
  independently in `BoardLayoutWindow._person_by_name` and
  `board_layout.person_for`. A rename breaks the link silently.

## Split responsibilities

- **CGM decode is in two places** — SIG CGM Measurement decode
  (`_sfloat_to_float`, `_decode_cgm_measurement`) lives in `ble_session.py:82-105`;
  the Dexcom / instant / CSV decodes are in `api/protocol.py`. "How a glucose
  byte becomes a number" depends on which profile.
- **CSV upload state machine** (BEGIN → ctrl-notify → DATA chunks → COMMIT →
  ctrl-notify, ABORT on failure) is entirely inside
  `ble_session._upload_csv_sets` / `_do_csv_upload` (`:392-456`), coupled to
  `protocol.CSV_CTRL_STATUS_OK` and the `_csv_ctrl_queue`.
- **Instant-event send path duplicated** — `_open_insert_food/_exercise` +
  `_send_instant` (`main_window.py:680-736`) and again inline in
  `_scenario_dispatch` (`:813-838`).

## Genuinely deep (for contrast — leave alone)

`models/cgm_metrics.py`, `models/dexcom_csv.py`, `models/food_log_csv.py` — pure,
clear contracts, reusable. All three currently untested (fixed by issue 10).
