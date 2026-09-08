# Fix PISA injection

Status: blocked (01)
Track: A
Phase: 1
Blocked by: 01

## Problem

`docs/TODO.md` lists "Arrumar o PISA — parece não estar funcionando", while
`PROTOCOL_SPEC.md:379-381` and `docs/E2E_TEST_PLAN.md` S7 claim it is
hardware-verified (40 % / 10 min bout drove the streamed value 100 → ~60 → 100).
A reviewer reading both catches the contradiction.

PISA multiplies the emitted glucose by
`1 - depth_frac * sin(pi * elapsed / duration)` after the noise model, on both
the model and CSV paths. App mirror: `SimulationEngine.add_instant_pisa` +
`_pisa_factor` (`engine.py:197-223`); firmware: `PISA instant` char `5b2c0013`,
`instant_pisa[]` in `model_thread.c`.

## What to do

1. Reproduce the failure — determine whether it is the firmware path, the app
   mirror (`engine.py`), or the graph shading (`MainWindow._pisa_spans` /
   `_draw_pisa_spans`).
2. Fix it. Keep the modest claim: "the simulator can inject a
   compression-artifact (PISA) fault" — do not overstate it as a published
   attenuation model (`docs/architecture_flows.tex:363-368` already frames it as
   a phenomenological, design-chosen curve).
3. Add the missing literature citation for the compression-low artifact
   (Facchinetti et al. or a CGM-accuracy review) — flagged at
   `docs/architecture_flows.tex:368`.

## Done when

- A pytest on the extracted engine step asserts the PISA envelope shape
  (factor 1 → `1-depth` at midpoint → 1) with the underlying glucose unchanged.
- Hardware re-check matches, and the E2E S7 assertion is real.
- The `docs/TODO.md` bug line is removed and the `.tex` carries the citation.
