"""Issue 10: clinical range-metrics maths (models.cgm_metrics)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from models import cgm_metrics


def test_empty_input_is_all_zero():
    m = cgm_metrics.compute([])
    assert m.n == 0
    assert (m.mean, m.sd, m.cv, m.tir_pct, m.tbr_pct, m.tar_pct) == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_bands_partition_100_percent():
    vals = [40, 60, 90, 120, 175, 200, 300]  # one in each of the 5 bands + extras in TIR
    m = cgm_metrics.compute(vals)
    assert m.tbr_pct + m.tir_pct + m.tar_pct == pytest.approx(100.0)
    assert m.tbr_pct == pytest.approx(m.tbr1_pct + m.tbr2_pct)
    assert m.tar_pct == pytest.approx(m.tar1_pct + m.tar2_pct)


def test_default_band_edges():
    # <54 TBR2, 54..70 TBR1, 70..180 TIR, 180..250 TAR1, >250 TAR2
    m = cgm_metrics.compute([53.9, 54.0, 70.0, 179.9, 180.0, 250.0, 250.1])
    assert m.tbr2_pct == pytest.approx(100 / 7)  # only 53.9
    assert m.tbr1_pct == pytest.approx(100 / 7)  # 54.0 (70.0 is TIR, exclusive upper)
    assert m.tir_pct == pytest.approx(300 / 7)  # 70.0, 179.9, 180.0
    assert m.tar1_pct == pytest.approx(100 / 7)  # 250.0
    assert m.tar2_pct == pytest.approx(100 / 7)  # 250.1


def test_mean_sd_cv_population_stats():
    m = cgm_metrics.compute([100.0, 100.0, 100.0, 100.0])
    assert m.mean == pytest.approx(100.0)
    assert m.sd == pytest.approx(0.0)
    assert m.cv == pytest.approx(0.0)

    m2 = cgm_metrics.compute([90.0, 110.0])  # population sd = 10
    assert m2.mean == pytest.approx(100.0)
    assert m2.sd == pytest.approx(10.0)
    assert m2.cv == pytest.approx(10.0)


def test_custom_thresholds_shift_the_bands():
    vals = [80.0] * 10
    tight = cgm_metrics.compute(vals, tbr1_below=90.0)  # 80 now counts as TBR1
    assert tight.tbr1_pct == pytest.approx(100.0)
    assert tight.tir_pct == pytest.approx(0.0)


# --- issue 13: time in range (h:mm) ---------------------------------------


def test_span_minutes_populates_time_fields():
    # 8 readings, 6 in range, over a 24 h (1440 min) window
    vals = [100.0] * 6 + [40.0, 300.0]
    m = cgm_metrics.compute(vals, span_minutes=1440.0)
    assert m.span_min == pytest.approx(1440.0)
    assert m.tir_min == pytest.approx(1440.0 * 6 / 8)  # 1080 min = 18:00
    assert m.tbr_min + m.tir_min + m.tar_min == pytest.approx(1440.0)
    assert m.tbr2_min == pytest.approx(1440.0 / 8)  # the single <54 reading


def test_time_fields_zero_without_span():
    m = cgm_metrics.compute([100.0, 100.0])
    assert m.span_min == 0.0
    assert (m.tir_min, m.tbr_min, m.tar_min) == (0.0, 0.0, 0.0)
    assert m.tir_pct == pytest.approx(100.0)  # pct still populated


@pytest.mark.parametrize(
    ("minutes", "text"),
    [(0.0, "0:00"), (5.0, "0:05"), (65.0, "1:05"), (754.0, "12:34"), (1440.0, "24:00")],
)
def test_fmt_hm(minutes, text):
    assert cgm_metrics.fmt_hm(minutes) == text
