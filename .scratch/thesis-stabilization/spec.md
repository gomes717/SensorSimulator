# Thesis stabilization — spec

Outcome of the `/grill-me` session + the codebase architecture review (2026-09-08).

## Goal

Make the existing simulator trustworthy and defensible for the TCC defense. The
**firmware is the deliverable** (two-thread RTOS, external-flash persistence,
standard GATT); the PyQt6 app is a correctness cross-check + demo. The **written
analysis is the deliverable**; the live demo is low-stakes. **No new features
beyond the items in `issues/`** — but every item there is in scope.

## Framing decisions

- Codebase owner: solo now, possibly a future student later → moderate test /
  structure investment, no gold-plating.
- Two tracks, used as a *sequencing guide*, not a wall:
  - **Track A** — correctness / verified behaviour. Ships with a regression test.
  - **Track B** — UX / structure. Batched into one pre-write-up pass, deferred
    behind Track A. Exception: the slot→name relabel rides along with the
    per-user-model work (issue 04), same files.
- **Done bar:** a fix that produces a thesis figure/number ships with a pytest or
  E2E case pinning it; everything else, manual verification is fine.

## Settled technical decisions

- **Tests:** tight pytest suite for the pure modules + the extracted engine step,
  **and** harden the existing hardware E2E with one pinning case per Track A bug.
  No GUI / BLE-mock coverage, no structural E2E rethink. `tests/` at repo root.
- **Python tooling (issue 21, `docs/CODING_STANDARDS.md`):** uv (full, `uv.lock`,
  no `requirements.txt`), ruff (lint + format, flake8 dropped), pylint trimmed to
  a design-smell detector, pyright `standard` (pure modules clean, rest ratcheted).
  All four run in a plain `scripts/hooks/pre-commit` git hook on every commit.
  `requires-python = ">=3.12"`.
- **Engine seam:** extract the per-tick logic into a pure step; the `QThread`
  becomes a thin driver. Only structural extraction in scope.
- **Per-user model:** one engine driver per occupied slot in `dict[slot, driver]`;
  single-sensor / no-board routes through the same dict with one entry. Rebuild
  all drivers wholesale on any board-layout change. Clock, speed, run-state are
  shared; pause/resume/stop fan out to every driver.
- **Param-order invariant:** each model module owns its `PARAM_NAMES`;
  `protocol`, `engine._ADAPTERS`, `person_config_window` import it. Plus a
  checked-in golden file of each model's firmware C-struct field order + a pytest
  asserting the Python tuple matches, hand-updated on struct changes.
- **Dexcom:** baseline reframe (issue 09) is Track A and always ships. Real G6
  compatibility (issue 12) is a stretch goal with a hard drop-dead date; Track A
  always outranks it and it is the first thing cut on any slippage.
- **Clinical metrics / results tables:** untouched until the pre-write-up phase
  (issues 13–15), after correctness is solid. Acknowledged as required for the
  final thesis.

## Phases

| Phase | Contents | Issues |
|---|---|---|
| 1 | Track A correctness + test net | 01–11 |
| 2 | Dexcom real G6 compat — stretch, drop-dead 2026-10-30 | 12 |
| 3 | Pre-write-up: metrics, results tables, UX cluster, minimal reorg | 13–17 |
| — | Architectural debt, documented for a future maintainer (backlog) | 18–20 |

## Open items

- **Defense: 2026-10-30** (moved from 15/09 on 2026-09-08). Issue 12 drop-dead = the defense date.
- **Q24 "both":** assumed to mean *G6 only* for the stretch + you do the format
  RE yourself + the `.tex` "out of scope" wording gets revised. Correct issue 12
  if that was wrong.

## Status vocabulary

`Status: ready` — startable now · `Status: blocked (NN)` — waiting on issue NN ·
`Status: backlog` — deferred (Phase 3 or architectural debt).

---

# Project reference (from the code, 2026-09-08)

Assembled from the architecture + docs sweeps. Grounds the issues above; not a
substitute for `docs/ARCHITECTURE.md` / `PROTOCOL_SPEC.md`.

## What the system is

A **CGM (continuous glucose monitor) sensor simulator**. A Nordic **nRF54L15 DK**
running Zephyr behaves like a real CGM sensor over BLE, driven by a physiological
model instead of a patient. A **PyQt6 desktop app** connects as the BLE client,
configures the board, plots what it streams, and runs the *same* model locally
(no sensor noise) as a live correctness cross-check.

- **The firmware is the thesis deliverable.** It independently proves: a
  two-thread RTOS design (model thread vs comm thread), non-volatile config on
  external SPI-NOR flash surviving reboots, and a real BLE GATT server (SIG
  standard CGM Service + a custom config service) working against an unmodified
  Windows BLE client (`bleak`).
- **The app is a client + demo**, not the deliverable. "Model Only" mode (no
  board) is a debugging/demo convenience.
- **The written analysis is the deliverable.** Today the docs are capability
  descriptions + qualitative hardware spot-checks — there are **no quantitative
  results** (no MARD, no error grid, no metric tables). Issue 15 addresses that.

## Core correctness invariant

Sensor noise is the *only* thing that should visibly separate the **expected**
line (local Python model) from the **received** line (board stream). Everything
else — the ODE, the integration method, the starting state, the parameter order
— is identical on both sides by construction. This invariant is what makes the
whole comparison meaningful, and several issues exist to protect it:

- 03 (high-speed ODE breakage would diverge the two integrators)
- 04 (one expected line per slot, so the cross-check holds for multi-sensor)
- 05 (a silent Python↔C param-order drift silently corrupts every comparison)

## Physiological models

Four, selectable per slot (`src/models/`, ported byte-for-byte from
`cgmsim/src/cgmsim_*.c`; param order must match the firmware C structs):

| Model | Notes |
|---|---|
| Cambridge / Hovorka | rate-fed meals, no exercise term |
| UVA/Padova (T1DMS) | rate-fed meals, `iir/60` basal, no exercise term |
| Roy & Parker | impulse-fed meals, has an exercise input |
| Deichmann | impulse-fed meals, heart-rate driven exercise |

`_ModelAdapter` (`engine.py:90-136`) gives them a uniform step signature. Three
sensor-noise models (Ideal / Breton / Facchinetti) run **firmware-side only**.

## Firmware (in-repo under `firmware/peripheral_cgms/`)

- **Model thread** — integrates the selected model(s) once per wall second;
  `dt_min = (1/60) * speed_mult`. Applies noise + PISA to the sensor reading.
- **Comm thread** — pushes readings to the active BLE profile (~500 ms poll).
- **`sim_config`** — persisted struct on external flash. Current top-level
  (v5): `magic / version / sensor_count / comm_profile / speed_mult` + `slots[4]`.
  Version has bumped with each feature (speed = v3, comm_profile = v4,
  multi-slot = v5).
- **Multi-sensor** — `CONFIG_APP_SENSOR_COUNT` (1–4, default 4). Each slot has
  its own BLE identity (fixed static address), advertising set, CGMS service
  instance, and independent config *or* a CSV. Clock + speed + run-state are
  global. `N == 1` is byte-identical to the single-sensor build.
- **BLE services**: SIG CGM Service `0x181F` (measurement `0x2AA7`); custom
  config service (`5b2c00xx` short keys); Dexcom-style service `0xFEBC` (name
  `DXCM01`, single-sensor only).
- **Known security reduction**: `CONFIG_APP_CGMS_NO_AUTH` (default on when
  `SENSOR_COUNT > 1`) drops the `*_AUTHEN` permissions — Windows cannot complete
  LE-SC against the board's non-default BLE identities, so the N identities
  stream unpaired/unencrypted. Documented as a real limitation in
  `docs/architecture_flows.tex:407-439`.

## App modules (`src/`)

| Dir | Holds |
|---|---|
| `api/` | `protocol.py` (~40 `encode_*` / `decode_*` codec funcs), `ble_uuids.py` |
| `core/` | `ble_message_log.py` (list + signal, pass-through) |
| `services/` | `ble_session.py` (one `QThread` per BLE address, asyncio loop, `bleak`), `bluetooth_scanner.py`, `windows_ble_pairing.py` |
| `models/` | model modules, `engine.py` (`SimulationEngine(QThread)` — one profile), `profile_store.py`, `board_layout.py`, `scenario.py`, `app_settings.py`, `cgm_metrics.py`, `dexcom_csv.py`, `food_log_csv.py`, `types.py` |
| `graphic/` | `main_window.py` (1456-line god object — see issue 18), ~15 config/utility windows |
| `utils/` | (empty) |

## Domain vocabulary

- **Slot** — one of the 1–4 independent sensor positions on the board. Selected
  for per-sensor config writes via the `Sensor select` cursor char (`5b2c0015`).
- **Identity** — a slot's BLE address + advertised name (`"Nordic Glucose
  Sensor {i+1}"`). The app demuxes streams by the trailing number.
- **Person profile / Sensor profile** — a saved `PersonProfile` /
  `SensorProfile` (`data/profiles.json`). A person carries model id + params +
  food/exercise schedule, or a CSV assignment.
- **Board layout** — slot → (person name, sensor name) mapping
  (`data/board_layout.json`), pushed to the board by `BoardLayoutWindow`.
- **Data source** — per slot: `model` (physiological) or `csv` (replay a
  recorded 24 h Dexcom window verbatim, food log report-only).
- **Comm profile** — global: `SIG CGMS` (default) or `Dexcom-style`
  (single-sensor only).
- **Instant event** — one-shot Insert Food / Insert Exercise / Insert PISA
  injected mid-run; does not persist, does not reset the clock.
- **PISA** — Pressure-Induced Sensor Attenuation. A phenomenological false-low:
  reading × `(1 - depth·sin(π·elapsed/duration))`, applied after noise, on the
  CSV source too. The only wired fault (fault panel is otherwise a scaffold).
- **Speed multiplier** — continuous x1–x1000; replaced the old on/off fast mode.
- **Run state** — `stopped | running | paused`. Held app-side as a string, an
  engine `_paused` bool, and a `protocol.RUN_STATE_*` int — three
  representations kept in sync by hand (issue 18).

## Data flow

- **Outbound (single device)**: `PersonProfile` → `profile_store` JSON → a config
  window → `protocol.encode_*` → `BleSession.queue_write(char_key, bytes)` →
  asyncio drain → `client.write_gatt_char` → firmware; then `restart_board()` and
  wait for the `reset_sync` notification.
- **Outbound (multi-slot)**: `BoardLayoutWindow` resolves names → profiles,
  encodes every characteristic, returns `list[dict]` →
  `BleSession.send_board_layout` writes the `sensor_select` cursor then each
  `(char_key, payload)` (paced, retry on GATT 0x11), leaves the board RUNNING.
  The engine is bypassed for multi-slot today (issue 04).
- **Outbound (CSV)**: `engine.load_csv_window()` → `dexcom_csv` + `food_log_csv`
  → `protocol.build_*_track` → `BleSession.start_csv_upload` → BEGIN / DATA
  chunks / COMMIT with CRC-32, acked via a control-notify queue.
- **Inbound**: firmware notification → `BleSession._handle_notification` branches
  by UUID (SIG measurement decoded locally; Dexcom / food-exercise / csv-control
  via `protocol.decode_*`) → emits a **loosely-typed `dict`** (no schema) →
  `BleMessageLog` re-emits → `MainWindow._on_new_message` destructures by string
  key, buffers per `user_id`, redraws if selected.
- **Readback**: `BleSession.request_read(char_key)` → `config_read` signal → the
  window's `protocol.decode_*`.

## Test surfaces

- **C model unit tests** — `cgmsim/tests/` (Cambridge, UVA/Padova, Roy&Parker,
  Deichmann, sensors).
- **Hardware E2E** — `scripts/e2e.py` (19 cases, single-sensor),
  `scripts/e2e_4sensor.py` (14 cases, multi-sensor), `scripts/ui_smoke.py`,
  `scripts/validate_ble_stream.py`. Plan: `docs/E2E_TEST_PLAN.md`. No CI (no
  bench board). "Let many errors through" — issue 11.
- **Python unit tests** — none today. Issue 10 adds `tests/` + `pytest` for the
  pure modules + the extracted engine step + the param-order golden check.
