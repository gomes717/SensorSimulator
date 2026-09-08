# Minimal folder reorg

Status: backlog
Track: B
Phase: 3
Blocked by: Phase 1 complete

## Problem

`docs/TODO.md`: "Arrumar a organização de pastas (`api`, `core`, `gui`,
`services`, `models`, `utils`)." Grill decision (Q4/Q9): the codebase is solo now
with a possible future-student handoff — do the **minimum** reorg that follows
from the Phase 1 seam work, not a repo-wide reshuffle.

## What to do

- Only the file moves implied by issue 01 (engine step extraction) and issue 05
  (param-order home) — e.g. a `models/engine_step.py` alongside `engine.py`,
  `tests/` at the repo root.
- `src/graphic/` is already the "gui" folder under a different name — rename to
  `gui/` only if it is a clean mechanical move with no import churn beyond
  find/replace. Otherwise leave it.
- Do **not** split `services/` or reshuffle `core/` / `utils/`.

## Done when

Imports still resolve, `pytest` + `scripts/ui_smoke.py` pass, and the tree
matches the Phase 1 work — nothing more.
