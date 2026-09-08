# Clinical metrics: switch % to time

Status: backlog
Track: B
Phase: 3
Blocked by: Phase 1 complete

## Problem

`docs/TODO.md`: "Trocar as métricas de % para tempo." (Professor review.)
`models/cgm_metrics.py` reports TIR/TBR1/TBR2/TAR1/TAR2 as percentages
(`docs/APPLICATION.md:139-141`, `:153`, `:159`). The docs report time-in-range
nowhere; the stated direction is percentage → time (h:mm), not both.

## What to do

- `cgm_metrics.compute()` returns time (minutes / `h:mm`) per range alongside or
  instead of %. Keep the computation pure and tested (issue 10).
- Update the three consumers: `MainWindow` live panel, `CsvAnalysisWindow`, the
  range-metrics readout.
- Update `docs/APPLICATION.md` and the E2E S10-02 assertion.

## Done when

The metrics panels and CSV Analysis show time-in-range; the pytest for
`cgm_metrics` covers the time output.
