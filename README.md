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
  UI components (sensor tabs and pages, run controls, commands, alerts)
- [`docs/adr/`](docs/adr/) — decision records for the app design (independent
  sensor pages, the board as the source of truth, tab alerts, the `MainWindow`
  decomposition)
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
- **Three fully independent sensors on one board** (`CONFIG_APP_SENSOR_COUNT=3`)
  — each its own BLE identity, model-or-CSV, noise,
  and schedule, assigned with the normal Person "Send to Board"; one shared sim
  clock + speed
- **One tab per sensor**, browser-style: each tab has its own graphs, history,
  stats and commands. A "+" after the last tab (like a browser) connects another sensor. Nothing connected → a centered "Connect Bluetooth" start
  screen; a dropped sensor's tab greys out and revives on reconnect; the tab's
  close button disconnects it
- **Tab alerts:** a warning icon for out-of-range readings, and a critical
  reading makes the tab blink red until you open it (then solid until it recovers)
- **Commands on every tab** — Food… / Exercise… / PISA… buttons that open a small
  form and send a one-shot event to *that* sensor without resetting the simulation; disabled (with the reason)
  until the run is going and the sensor's link is live, hidden for a CSV sensor
- **Start / Pause / Stop in the toolbar**, blocked until a sensor is connected
  (Model Only needs none)
- The display follows what the **board** reports each sensor is running (CSV
  replay or a named model), not what the app last tried to send
- Replay a recorded 24 h CGM trace (Dexcom CSV) from the board instead of a
  model — uploaded over BLE, stored in the board's external flash
- Simulation speed of 1 second per second (real time) or 1 minute per second (x60)
  in the app (the firmware still accepts any multiplier up to x1000); rolling "last 1 hour"
  graph view (or the whole run)
- PISA (pressure-induced sensor attenuation) injection with the affected
  interval shaded on that sensor's graph
- BLE profile: standard SIG CGMS (a basic Dexcom-style stream exists in the firmware and
  protocol; the app's selector for it was removed pending a refactor)
- Scan for and connect to several nearby BLE devices at once, with
  automatic pairing for devices needing it (Windows)
- Link-health reporting: a dropped link greys the tab, a connected-but-silent
  sensor shows "No data" and is re-armed automatically
- Glucose graph — solid line from the board (coloured by range, changing colour
  exactly where it crosses a limit; optional per-sample dots), dashed line from the
  app's own parallel simulation of the model the board reports, for a live
  correctness check
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
│   ├── gui/          # The UI: sensor tabs + pages, run controls, windows
│   └── utils/        # Reserved for generic helpers
├── scenarios/        # JSON timed-action scripts used by the E2E harness (not the app)
├── scripts/          # e2e.py (single-sensor harness), e2e_4sensor.py (multi-sensor harness),
│                     #   e2e_long_3sensor.py / e2e_overnight_3sensor.py (long runs),
│                     #   ble_soak.py / ble_ab_batch.py / watchdog_check.py (BLE reliability),
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

If `uv` isn't found (a terminal opened before `uv` was installed — its PATH is
stale; fully reopen it or reboot), use the wrappers, which locate `uv` by known
paths. From the repo root:

| Shell | Command |
|---|---|
| PowerShell | `.\run.ps1` |
| cmd.exe | `run` |
| Git Bash | `./run.sh` |

They pass arguments through, so `.\run.ps1 -m pytest -q` == `uv run python -m pytest -q`.

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
