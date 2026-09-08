# UX cluster (batched, pre-write-up)

Status: partial (2026-09-08, commit 1d0749b)
Track: B
Phase: 3

## Done

- Main glucose legend labels the coloured trace (In range / Borderline / Low-High).
- Range bands 0.16 -> 0.09 alpha, PISA span 0.15 -> 0.10, CSV bands + selection span likewise.
- Glucose graph title bold 11 pt (which person/sensor is plotted).

## Still open (need a design call from the user)

- Configuration window layout declutter.
- Avatar style ("Mudar o avatar").

---

(orig)
Status: backlog
Track: B
Phase: 3
Blocked by: Phase 1 complete

## Scope

One batched pass over the readability items from `docs/TODO.md` (several from
professor review). Do them together — they touch the same graph / config code.

- **Graph legend** — "Arrumar legenda dos gráficos."
- **Region transparency** — "Deixar as regiões do gráfico mais transparentes"
  (the TBR/TIR/TAR bands + PISA spans; `MainWindow._apply_range_bands`,
  `_draw_pisa_spans`).
- **Selection visibility** — "Melhorar a visibilidade de qual pessoa/sensor está
  sendo apresentado no gráfico" (graph title + tree highlight already partial at
  `main_window.py:1312-1329`).
- **Config window declutter** — "Melhorar a janela de configuração, que está
  confusa" (`configuration_window.py` — 4 groups stacked; see also the seam-leak
  debt in issue 18).
- **Avatar** — "Mudar o avatar" (`src/graphic/avatar.py`).

The slot→name relabel is **not** here — it is folded into issue 04.

## Done when

The batch is merged; a reviewer reading the app can tell at a glance which
line/user is shown, what the bands mean, and the config window is navigable.
Verify with `scripts/ui_smoke.py` + a manual pass.
