"""Placeholder suite so the gate has something to run.

Real coverage for the pure modules lands in
`.scratch/thesis-stabilization/issues/10-pytest-suite-pure-modules.md`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_pure_modules_import():
    from api import protocol  # noqa: F401
    from models import cgm_metrics, dexcom_csv, food_log_csv  # noqa: F401


def test_cgm_metrics_basic():
    from models import cgm_metrics

    m = cgm_metrics.compute([65.0, 80.0, 120.0, 200.0, 260.0])
    assert m.n == 5
    assert m.tir_pct == 40.0  # 80 and 120 in the 70-180 band
    assert round(m.mean, 1) == 145.0


def test_protocol_glucose_track_roundtrip():
    import struct

    from api import protocol

    blob = protocol.build_glucose_track([100.5, -40000.0, 40000.0])
    assert struct.unpack("<3h", blob) == (100, -32768, 32767)
