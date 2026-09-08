# Hide the food/exercise graph when the data source is CSV

Status: ready
Track: A
Phase: 1
Blocked by: —

## Problem

`docs/TODO.md`: "Quando a fonte for CSV, não mostrar o gráfico de comida."
In CSV playback there is no physiological model running — the food log is
consumed *report-only* and does not affect glucose (`engine.py:256-317`,
`docs/TODO.md:5`). The food/exercise graph in that mode shows a carb-rate curve
that implies causation that isn't there — a correctness/clarity bug, not
cosmetics.

## What to do

When the selected user's `data_source == "csv"`, hide (or clearly mark as
"report-only, not driving glucose") the food/exercise graph in `MainWindow`.
Decide which: hiding is simpler; a labelled panel keeps the food-log timing
visible. Recommend hiding unless the food-log timing is needed for a demo.

## Done when

Switching a user to CSV hides/relabels the food graph; switching back to model
restores it. Covered by `scripts/ui_smoke.py` or a widget-state assertion.
