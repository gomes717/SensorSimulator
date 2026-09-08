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
| 2 | Dexcom real G6 compat — stretch, drop-dead 2 weeks before the draft is due | 12 |
| 3 | Pre-write-up: metrics, results tables, UX cluster, minimal reorg | 13–17 |
| — | Architectural debt, documented for a future maintainer (backlog) | 18–20 |

## Open items

- **Defense / draft-due date: TBD** — fill the concrete drop-dead date into issue 12.
- **Q24 "both":** assumed to mean *G6 only* for the stretch + you do the format
  RE yourself + the `.tex` "out of scope" wording gets revised. Correct issue 12
  if that was wrong.

## Status vocabulary

`Status: ready` — startable now · `Status: blocked (NN)` — waiting on issue NN ·
`Status: backlog` — deferred (Phase 3 or architectural debt).
