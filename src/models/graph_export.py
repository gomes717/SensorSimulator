"""The CSV a sensor's graph exports: every plotted series on one simulated-time column.

One row per distinct timestamp (simulated seconds, as on the graph's x axis); a series that has
no sample at that time leaves its cell empty, as does a NaN. Pure data in, text out, so it is
tested without a window.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Sequence
from pathlib import Path

HEADER = ("time_s", "glucose_mg_dl", "expected_mg_dl", "carbs_g_per_min", "exercise_pct")


def rows(
    received: tuple[Sequence[float], Sequence[float]],
    expected: tuple[Sequence[float], Sequence[float]],
    food_ex: tuple[Sequence[float], Sequence[float], Sequence[float]],
) -> list[tuple[float, float | None, float | None, float | None, float | None]]:
    """Merge the received ``(t, glucose)``, expected ``(t, glucose)`` and food/exercise
    ``(t, carbs, exercise)`` series into rows ordered by time."""
    merged: dict[float, list[float | None]] = {}

    def put(t: float, column: int, value: float) -> None:
        cells = merged.setdefault(t, [None, None, None, None])
        cells[column] = None if math.isnan(value) else value

    for t, glucose in zip(*received, strict=True):
        put(t, 0, glucose)
    for t, glucose in zip(*expected, strict=True):
        put(t, 1, glucose)
    for t, carbs, exercise in zip(*food_ex, strict=True):
        put(t, 2, carbs)
        put(t, 3, exercise)
    return [(t, *merged[t]) for t in sorted(merged)]  # type: ignore[misc]


def write_csv(path: str | Path, table: Sequence[Sequence[float | None]]) -> None:
    """Write *table* under :data:`HEADER`; a ``None`` cell is left empty."""
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for row in table:
            writer.writerow("" if cell is None else cell for cell in row)
