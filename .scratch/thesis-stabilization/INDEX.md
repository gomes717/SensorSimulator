# Thesis stabilization — issue index

See `spec.md` for framing and decisions.

## Phase 1 — Track A correctness + test net

| # | Title | Status |
|---|---|---|
| 01 | Extract the engine per-tick logic into a pure step | **done** (64dd374) |
| 02 | Fix PISA injection | **done** (c95e94a) � verified working; see notes |
| 03 | Fix the speed multiplier breaking ODE integration | **done** (8873dfd) |
| 04 | One simulation model per user / slot (+ slot→name relabel) | **done** (1f06d1c + ec6c28e) |
| 05 | Collapse the model param-order invariant to one source | **done** (27a200b) |
| 06 | User still shows connected after disconnect | **done** (2438d17) |
| 07 | BLE timeslot contention between sensor identities | **done** (see notes) |
| 08 | Hide the food/exercise graph in CSV mode | **done** (45a0892) |
| 09 | Dexcom protocol — honest baseline reframe | **done** (308ebeb) |
| 10 | Stand up a pytest suite for the pure modules | **done** (e606880) |
| 11 | Harden the E2E suite | **done** (07333e4) — pins for 01/02/03/05/06/08 |
| 21 | Python tooling & coding standard (uv, ruff, pylint, pyright) | **done** (381d9c8 + 48c7516) |

## Phase 2 — stretch

| # | Title | Status |
|---|---|---|
| 12 | Dexcom real G6 compatibility | ready — drop-dead **2026-09-15**, Android confirmed |

## Phase 3 — pre-write-up

| # | Title | Status |
|---|---|---|
| 13 | Clinical metrics: % → time | **done** (298e7a0) |
| 14 | Global metric panel in CSV Analysis | **done** (cdf28f6) |
| 15 | Results tables: cross-model × cross-noise | **done** (7f162af) — offline table + MARD; board pass pending |
| 16 | UX cluster | **partial** (1d0749b) — legend/transparency/title done; config-declutter + avatar need a design call |
| 17 | Minimal folder reorg | **done** (fc6b51c) — src/graphic/ → src/gui/ |

## Backlog — architectural debt (documented, not scheduled)

| # | Title |
|---|---|
| 18 | MainWindow god object + seam leaks | **deferred** — too risky pre-defense |
| 19 | Shallow modules + split responsibilities | **partial** (05ae500) — name→slot + CSV-upload dedup'd |
| 20 | No app-level test coverage below "launch the app" | **partial** (743b5f6) — BleSession decode seam + 113 tests |
