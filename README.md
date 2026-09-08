# SensorSimulator

A TCC (undergraduate thesis) project: an nRF54L15 DK running Zephyr RTOS
firmware simulates a Continuous Glucose Monitoring (CGM) sensor over BLE —
streaming glucose readings driven by a real physiological model (one of
four) instead of a real patient — paired with a PyQt6 desktop app that
configures the simulated patient, connects over BLE, and displays live
readings against the same model run locally as a cross-check.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system overview: the two
  halves, how they exchange data, both sides' clocks
- [`docs/FIRMWARE.md`](docs/FIRMWARE.md) — RTOS threads, the MCU timestep,
  flash storage, BLE/CGMS
- [`docs/APPLICATION.md`](docs/APPLICATION.md) — app layers, threading,
  UI components
- [`docs/MODELS.md`](docs/MODELS.md) — the four physiological models and
  three sensor noise models: method, parameters, citations
- [`PROTOCOL_SPEC.md`](PROTOCOL_SPEC.md) — authoritative BLE wire format
  (every characteristic, byte layout)
- [`cgmsim/FORMULAS.md`](cgmsim/FORMULAS.md) — full ODEs and parameter
  tables for every model/sensor
- [`firmware/README.md`](firmware/README.md) — building/flashing the
  firmware
- [`docs/BLE_PAYLOAD_VALIDATION.md`](docs/BLE_PAYLOAD_VALIDATION.md) — our
  CGM wire format vs. a real Dexcom's; how the stream is cross-checked
- [`docs/E2E_TEST_PLAN.md`](docs/E2E_TEST_PLAN.md) — full app↔MCU test
  matrix + the failure-artifact / log-capture harness
- [`docs/FEATURE_IDEAS.md`](docs/FEATURE_IDEAS.md) — candidate features,
  effort/risk, suggested order

## Features

- Configure a simulated patient (physiological model + parameters), CGM
  sensor noise model, and a recurring daily food/exercise schedule; send it
  to the board or run it locally with no hardware ("Model Only" mode)
- **Up to 4 fully independent sensors on one board** (`CONFIG_APP_SENSOR_COUNT`)
  — each its own BLE identity, model-or-CSV, noise, and schedule; assigned
  from the Board Layout window; one shared sim clock + speed
- Insert one-shot food/exercise/PISA events into an already-running
  simulation (any one slot) without resetting it
- Replay a recorded 24 h CGM trace (Dexcom CSV) from the board instead of a
  model — uploaded over BLE, stored in the board's external flash
- Continuous x1–x1000 simulation-speed multiplier; rolling "last 1 hour"
  graph view (or the whole run)
- Fault injection (PISA / compression low) with the affected interval shaded
- Scenario runner — JSON files of timed actions (`scenarios/`), fired from the
  Scenario window
- Switchable BLE profile: standard SIG CGMS or a basic Dexcom-style stream
- Scan for and connect to several nearby BLE devices at once, with
  automatic pairing for devices needing it (Windows)
- Live treeview of connected devices with their latest glucose reading
- Glucose graph — solid line from the board, dashed line from the app's own
  parallel simulation of the same model, for a live correctness check
- Food/exercise graph — carb intake rate and exercise intensity over time
- Debug window listing every BLE message received, with detail view on click

## Project structure

```
SensorSimulator/
├── src/
│   ├── main.py       # Entry point
│   ├── api/          # BLE wire-format contract
│   ├── services/     # BLE I/O — sessions, scanning, pairing
│   ├── core/         # Shared message log
│   ├── models/       # Physiological models, sensor params, profile persistence
│   ├── gui/          # All windows/dialogs (the UI)
│   └── utils/        # Reserved for generic helpers
├── scenarios/        # JSON timed-action scripts for the Scenario window
├── scripts/          # e2e.py (single-sensor harness), e2e_4sensor.py (4-sensor harness),
│                     #   ui_smoke.py, validate_ble_stream.py
├── firmware/         # peripheral_cgms firmware source (git-tracked, builds/flashes from here)
├── cgmsim/           # Standalone CLI simulator — source of truth for the model math
├── data/             # Saved profiles.json, board_layout.json, settings.json
├── docs/             # Architecture & design docs (see above)
├── pyproject.toml    # Project metadata + tool config (uv, ruff, pylint, pyright)
├── uv.lock           # Locked dependency set
└── .gitignore
```

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                              # create .venv and install everything
git config core.hooksPath scripts/hooks   # enable the pre-commit gate (once per clone)
```

## Running

```bash
uv run python src/main.py
```

## Checks

See `docs/CODING_STANDARDS.md`. The pre-commit hook runs all four; the same
commands are the manual "full gate":

```bash
uv run ruff format --check .
uv run ruff check .
uv run pylint src
uv run pyright
uv run pytest
```
