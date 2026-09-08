# Global metric panel in the CSV Analysis window

Status: backlog
Track: B
Phase: 3
Blocked by: Phase 1 complete

## Problem

`docs/TODO.md`: "Adicionar métrica global na janela de CSV Analysis." The window
currently shows per-window range metrics (`docs/APPLICATION.md:159`); there is no
whole-recording summary.

## What to do

Add a panel in `src/graphic/csv_analysis_window.py` showing the metrics computed
over the entire loaded recording (not just the assigned 24 h window): TIR/TBR/TAR
(as time per issue 13), mean, SD, CV. Reuse `cgm_metrics.compute()` — no new
metric math.

## Done when

The CSV Analysis window shows a labelled "whole recording" metrics block distinct
from the per-window block.
