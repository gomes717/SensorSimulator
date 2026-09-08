# Stand up a pytest suite for the pure modules

Status: done (2026-09-08, commit e606880)
Track: A
Phase: 1
Blocked by: —

## Outcome

`tests/` now 83 passing (was 0): cgm_metrics (bands/edges/stats), dexcom_csv + food_log_csv readers (fixtures with real headers, ValueError paths), protocol encode/decode round-trips + clamping + chunk reassembly + dexcom decode. Engine-step coverage came with 01/03.

---

## Problem

The Python app has **zero unit tests** (architecture review §0). The only
automated tests are C model tests in `cgmsim/tests/` and hardware-in-the-loop
scripts in `scripts/`. The pure, reusable modules — `models/cgm_metrics.py`,
`models/dexcom_csv.py`, `models/food_log_csv.py`, and `api/protocol.py`'s
encode/decode pairs — are validated only indirectly, through "launch the app".

## What to do

- Add `tests/` at the repo root, `pytest`, add it to `requirements.txt` /
  `pyproject.toml`.
- Cover:
  - `cgm_metrics.compute()` — TIR/TBR/TAR/mean/variance/SD/CV, empty-input
    contract, threshold edges (`cgm_metrics.py:36-75`).
  - `dexcom_csv` — `read_egv`, `resample`, forward-fill, `ValueError` on bad
    rows.
  - `food_log_csv` — `read_food_log`, `slice_window`, `matching_food_log_path`.
  - `protocol` — every `encode_* / decode_*` pair round-trips; field order and
    struct sizes are asserted against `PROTOCOL_SPEC.md`.
  - the extracted engine step (issue 01) — deterministic multi-step runs.
- No GUI or BLE-mock coverage.

## Done when

`pytest` runs green locally and the pure modules + engine step have meaningful
assertions (not just import-smoke).
