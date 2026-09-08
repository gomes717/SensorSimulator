"""Issue 15: the offline results-table builder is deterministic; MARD is sane."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest
import results_tables as rt


def test_mard_zero_for_identical_series():
    ref = [100.0, 120.0, 90.0]
    assert rt.mard(ref, list(ref)) == pytest.approx(0.0)


def test_mard_matches_hand_calc():
    # |110-100|/100 + |90-100|/100 -> mean 0.10 -> 10 %
    assert rt.mard([100.0, 100.0], [110.0, 90.0]) == pytest.approx(10.0)


def test_mard_ignores_nonpositive_reference():
    assert rt.mard([0.0, 100.0], [999.0, 110.0]) == pytest.approx(10.0)


def test_offline_table_is_deterministic_and_complete():
    a = rt.offline_table()
    b = rt.offline_table()
    assert [r["row"] for r in a] == ["Cambridge", "Uva_Padova", "Royparker", "Deichmann"]
    assert a == b  # no RNG, no clock — reproducible for the thesis
    for row in a:
        assert row["n"] == str(rt.HORIZON_MIN)
        assert ":" in row["tir"]  # h:mm rendered
        assert 20.0 < float(row["mean"]) < 600.0
