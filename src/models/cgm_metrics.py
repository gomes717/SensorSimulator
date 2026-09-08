"""Clinical CGM range metrics (TIR/TBR/TAR, mean, variance) over a list of glucose values.

Pure: no Qt, no I/O. Shared by the CSV Analysis window and the main window's
live range-metrics panel. Band edges follow the common consensus targets
(mg/dL): TBR2 <54, TBR1 54-70, TIR 70-180, TAR1 180-250, TAR2 >250.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

DEFAULT_TBR2_BELOW = 54.0
DEFAULT_TBR1_BELOW = 70.0
DEFAULT_TAR1_ABOVE = 180.0
DEFAULT_TAR2_ABOVE = 250.0


@dataclass
class GlucoseMetrics:
    """Summary of one glucose series. Percentages are 0-100 and sum to ~100."""

    n: int
    mean: float
    variance: float  # population variance
    sd: float
    cv: float  # coefficient of variation, % (sd / mean * 100)
    tir_pct: float
    tbr_pct: float
    tbr1_pct: float
    tbr2_pct: float
    tar_pct: float
    tar1_pct: float
    tar2_pct: float


def compute(
    values: Iterable[float],
    *,
    tbr2_below: float = DEFAULT_TBR2_BELOW,
    tbr1_below: float = DEFAULT_TBR1_BELOW,
    tar1_above: float = DEFAULT_TAR1_ABOVE,
    tar2_above: float = DEFAULT_TAR2_ABOVE,
) -> GlucoseMetrics:
    """Return range/spread metrics for *values*. Empty input yields all-zero metrics."""
    vals = [float(v) for v in values]
    n = len(vals)
    if n == 0:
        return GlucoseMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    mean = sum(vals) / n
    variance = sum((v - mean) ** 2 for v in vals) / n
    sd = variance**0.5
    cv = (sd / mean * 100.0) if mean else 0.0

    tbr2 = sum(1 for v in vals if v < tbr2_below)
    tbr1 = sum(1 for v in vals if tbr2_below <= v < tbr1_below)
    tar2 = sum(1 for v in vals if v > tar2_above)
    tar1 = sum(1 for v in vals if tar1_above < v <= tar2_above)
    tir = n - tbr2 - tbr1 - tar1 - tar2

    def pct(c: int) -> float:
        return c / n * 100.0

    return GlucoseMetrics(
        n=n,
        mean=mean,
        variance=variance,
        sd=sd,
        cv=cv,
        tir_pct=pct(tir),
        tbr_pct=pct(tbr1 + tbr2),
        tbr1_pct=pct(tbr1),
        tbr2_pct=pct(tbr2),
        tar_pct=pct(tar1 + tar2),
        tar1_pct=pct(tar1),
        tar2_pct=pct(tar2),
    )
