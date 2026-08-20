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

## Features

- Configure a simulated patient (physiological model + parameters), CGM
  sensor noise model, and a recurring daily food/exercise schedule; send it
  to the board or run it locally with no hardware ("Model Only" mode)
- Insert one-shot food/exercise events into an already-running simulation
  without resetting it
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
│   ├── graphic/      # All windows/dialogs (the UI)
│   └── utils/        # Reserved for generic helpers
├── firmware/         # peripheral_cgms firmware source (git-tracked, builds/flashes from here)
├── cgmsim/           # Standalone CLI simulator — source of truth for the model math
├── data/             # Saved profiles.json
├── docs/             # Architecture & design docs (see above)
├── requirements.txt
├── pyproject.toml    # Pylint configuration
└── .gitignore
```

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Running

```bash
python src/main.py
```

## Linting

```bash
pylint src/
```
