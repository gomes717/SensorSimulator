# Results tables: cross-model × cross-noise metric comparison

Status: backlog
Track: B
Phase: 3
Blocked by: Phase 1 complete, 13

## Problem

The docs contain **zero quantitative results** — no metric tables, no plots, no
accuracy figures anywhere (architecture review, docs sweep). The thesis needs a
results chapter. This is *analysis*, not a new feature: run the existing
`cgm_metrics.compute()` over expected vs received for each of the four models ×
each noise model (Ideal / Breton / Facchinetti) and tabulate.

## What to do

- A script (`scripts/results_tables.py` or a notebook) that, for each
  model × noise combination, runs a fixed scenario and emits a table:
  TIR/TAR/TBR (time), mean, SD, CV for expected and received, plus the
  expected−received deltas.
- Decide **explicitly**: a single MARD number is ~15 lines on the existing
  expected/received arrays (`docs/FEATURE_IDEAS.md:303-312` calls MARD "the
  single most expected figure in a CGM-sensor thesis"). In or out? Default: in,
  because it is cheap and expected — but it was not on the original TODO, so it
  needs a conscious yes.
- Feed the tables into the write-up.

## Done when

Reproducible tables exist for all model × noise combinations, and the
MARD-in-or-out decision is recorded here.

## Note

MAGE, GMI, Clarke/Consensus error grid remain out of scope unless separately
decided (`docs/FEATURE_IDEAS.md` #1, #23).
