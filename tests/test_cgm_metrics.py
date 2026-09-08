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
