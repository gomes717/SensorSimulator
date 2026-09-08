# UX cluster (batched, pre-write-up)

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
