# Architectural debt: no app-level test coverage below "launch the app"

Status: partially addressed by issue 10 (rest is backlog)
Track: B
Phase: Phase 1 covers the pure modules + engine step; the rest is post-thesis
Blocked by: —

Source: architecture review §0, 2026-09-08.

## State

- **No Python unit tests exist.** No `test_*.py`, `conftest.py`, `pytest.ini`
  anywhere under `src/` or the repo root. `pyproject.toml` has only a
  `[tool.pylint]` block. `requirements.txt`: `PyQt6, matplotlib, pylint, bleak`.
- The only automated tests: C model tests in `cgmsim/tests/`, and
  hardware-in-the-loop scripts (`scripts/e2e.py`, `e2e_4sensor.py`, `ui_smoke.py`,
  `validate_ble_stream.py`).
- The Python physiological ports are validated only indirectly (via their C
  twins + full-stack E2E). `engine.py` orchestration, `protocol.py` codec, and
  the entire GUI layer have **no coverage below "launch the app"**.

## What Phase 1 fixes (issue 10)

`tests/` + `pytest`; `cgm_metrics`, `dexcom_csv`, `food_log_csv`, `protocol`
round-trips, the extracted engine step, the param-order golden test.

## What stays uncovered (accepted for the defense)

- GUI behaviour (`MainWindow` and the config windows) — only `ui_smoke.py`.
- `BleSession` — no BLE mock; only hardware E2E.
- The `new_message` dict schema — no contract test (schema itself deferred, see
  issue 18).

A future maintainer wanting a real safety net starts by giving `BleSession` a
seam that does not need a radio.
