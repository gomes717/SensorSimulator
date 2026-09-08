# Thesis stabilization — issue index

See `spec.md` for framing and decisions.

## Phase 1 — Track A correctness + test net

| # | Title | Status |
|---|---|---|
| 01 | Extract the engine per-tick logic into a pure step | **done** (64dd374) |
| 02 | Fix PISA injection | **done** (c95e94a) � verified working; see notes |
| 03 | Fix the speed multiplier breaking ODE integration | ready (01 done) |
| 04 | One simulation model per user / slot (+ slot→name relabel) | ready (01 done) |
| 05 | Collapse the model param-order invariant to one source | **done** (27a200b) |
| 06 | User still shows connected after disconnect | **done** (2438d17) |
| 07 | BLE timeslot contention between sensor identities | ready |
| 08 | Hide the food/exercise graph in CSV mode | **done** (45a0892) |
| 09 | Dexcom protocol — honest baseline reframe | ready |
| 10 | Stand up a pytest suite for the pure modules | ready |
| 11 | Harden the E2E suite | ready |
| 21 | Python tooling & coding standard (uv, ruff, pylint, pyright) | **done** (381d9c8 + 48c7516) |

## Phase 2 — stretch

| # | Title | Status |
|---|---|---|
| 12 | Dexcom real G6 compatibility | blocked (Phase 1 + time) — **drop-dead date TBD** |

## Phase 3 — pre-write-up

| # | Title | Status |
|---|---|---|
| 13 | Clinical metrics: % → time | backlog |
| 14 | Global metric panel in CSV Analysis | backlog |
| 15 | Results tables: cross-model × cross-noise | backlog |
| 16 | UX cluster (legend, transparency, selection, config declutter, avatar) | backlog |
| 17 | Minimal folder reorg | backlog |

## Backlog — architectural debt (documented, not scheduled)

| # | Title |
|---|---|
| 18 | MainWindow god object + window/state seam leaks |
| 19 | Shallow modules + split responsibilities |
| 20 | No app-level test coverage below "launch the app" |
